import logging
from datetime import datetime, timedelta
from itertools import zip_longest
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import (
    Case,
    Count,
    F,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import (
    ApiUser,
    CarMake,
    CarModel,
    Conversation,
    ConversationParticipant,
    Message,
    MessageAttachment,
    MessageStatus,
    MobileDevice,
    PartImage,
    PartRequest,
    PartRequestAccess,
    PartRequestStatus,
    SparePart,
    UserReport,
)
from .models import Payment, Subscription
from .pagination import MessageCursorPagination
from .serializers import (
    ApiUserSerializer,
    CarMakeSerializer,
    CarModelSerializer,
    ConversationListSerializer,
    ConversationParticipantSerializer,
    ConversationSerializer,
    FirebasePasswordResetSerializer,
    FirebaseRegistrationSerializer,
    MeSerializer,
    MobileDeviceSerializer,
    MessageCreateSerializer,
    MessageListSerializer,
    MessageStatusSerializer,
    PartImageSerializer,
    PartRequestAccessSerializer,
    PartRequestSerializer,
    PartRequestStatusSerializer,
    PublicUserProfileSerializer,
    SparePartSerializer,
    UserReportAdminUpdateSerializer,
    UserReportCreateSerializer,
    UserReportSerializer,
    normalize_phone_number,
)
from .car_catalog_sync import CarCatalogSyncService, CarImagesApiError
from .app_store_versions import latest_store_version
from .translation import (
    localize_conversation_response_data,
    localize_message_response_data,
    localize_part_request_response_data,
    resolve_requested_translation_language,
    translate_car_model_search_query,
)
from chat.broadcasting import (
    broadcast_chat_event,
    broadcast_created_message,
    broadcast_inbox_message,
)
from chat.services import (
    create_message_with_statuses,
    delete_message_for_everyone,
    get_default_delivered_user_ids,
    hide_message_for_user,
    update_text_message,
)
from chat.push_notifications import (
    send_chat_message_push_notifications,
    send_request_created_push_notifications,
    send_test_request_notification,
)

from .account_deletion import delete_account


logger = logging.getLogger(__name__)
_car_catalog_sync_service = CarCatalogSyncService()


def _is_admin_user(user):
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and (user.is_staff or user.is_superuser)
    )


def _ensure_admin_user(user):
    if not _is_admin_user(user):
        raise PermissionDenied("Only admin users can perform this action.")


def _can_view_all_chats(user):
    return bool(
        _is_admin_user(user)
        and getattr(user, "admin_can_view_all_chats", False)
    )


def create_and_broadcast_system_chat_message(*, conversation_id, sender, text):
    delivered_user_ids = get_default_delivered_user_ids(conversation_id) - {sender.id}
    payload, status_events = create_message_with_statuses(
        conversation_id=conversation_id,
        sender=sender,
        text=text,
        message_type="text",
        client_timestamp=timezone.now(),
        delivered_user_ids=delivered_user_ids,
    )
    broadcast_created_message(payload, status_events)
    broadcast_inbox_message(payload)
    send_chat_message_push_notifications(payload)
    return payload


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return JsonResponse({"status": "ok"})


@api_view(["GET"])
@permission_classes([AllowAny])
def car_catalog(request):
    makes = CarMake.objects.prefetch_related(
        Prefetch(
            "models",
            queryset=CarModel.objects.filter(is_active=True).order_by("name"),
            to_attr="active_models",
        )
    ).order_by("name")
    serializer = CarMakeSerializer(makes, many=True, context={"request": request})
    return Response(serializer.data)


def _version_parts(value):
    normalized = str(value or "").strip()
    if not normalized:
        return []

    parts = []
    for raw_part in normalized.replace("+", ".").replace("-", ".").split("."):
        try:
            parts.append(int(raw_part))
        except ValueError:
            break
    return parts


def _is_newer_version(candidate, current):
    candidate_parts = _version_parts(candidate)
    current_parts = _version_parts(current)
    if not candidate_parts or not current_parts:
        return False

    for candidate_part, current_part in zip_longest(candidate_parts, current_parts, fillvalue=0):
        if candidate_part > current_part:
            return True
        if candidate_part < current_part:
            return False
    return False


def _is_newer_build(candidate, current):
    if candidate is None:
        return False
    try:
        current_build = int(str(current or "").strip())
    except ValueError:
        return False
    return candidate > current_build


def _platform_update_settings(platform):
    normalized_platform = str(platform or "").strip().lower()
    if normalized_platform == "ios":
        return {
            "latest_version": latest_store_version(
                "ios", settings.APP_UPDATE_LATEST_IOS_VERSION
            ),
            "latest_build_number": settings.APP_UPDATE_LATEST_IOS_BUILD,
            "minimum_supported_version": settings.APP_UPDATE_MIN_IOS_VERSION,
            "minimum_supported_build_number": settings.APP_UPDATE_MIN_IOS_BUILD,
            "store_url": settings.APP_UPDATE_IOS_STORE_URL,
        }

    return {
        "latest_version": latest_store_version(
            "android", settings.APP_UPDATE_LATEST_ANDROID_VERSION
        ),
        "latest_build_number": settings.APP_UPDATE_LATEST_ANDROID_BUILD,
        "minimum_supported_version": settings.APP_UPDATE_MIN_ANDROID_VERSION,
        "minimum_supported_build_number": settings.APP_UPDATE_MIN_ANDROID_BUILD,
        "store_url": settings.APP_UPDATE_ANDROID_STORE_URL,
    }


@api_view(["GET"])
@permission_classes([AllowAny])
def app_update(request):
    platform = request.query_params.get("platform", "android")
    current_version = request.query_params.get("version", "")
    current_build = request.query_params.get("build", "")
    update_settings = _platform_update_settings(platform)

    update_available = (
        _is_newer_version(update_settings["latest_version"], current_version)
        or _is_newer_build(update_settings["latest_build_number"], current_build)
    )
    update_required = (
        _is_newer_version(update_settings["minimum_supported_version"], current_version)
        or _is_newer_build(update_settings["minimum_supported_build_number"], current_build)
    )

    payload = {
        "update_available": update_available or update_required,
        "update_required": update_required,
        "latest_version": update_settings["latest_version"] or None,
        "latest_build_number": update_settings["latest_build_number"],
        "minimum_supported_version": update_settings["minimum_supported_version"] or None,
        "minimum_supported_build_number": update_settings["minimum_supported_build_number"],
        "title": settings.APP_UPDATE_TITLE or None,
        "message": settings.APP_UPDATE_MESSAGE or None,
        "release_notes": settings.APP_UPDATE_RELEASE_NOTES or None,
        "store_url": update_settings["store_url"] or None,
    }
    if str(platform or "").strip().lower() == "ios":
        payload["ios_store_url"] = update_settings["store_url"] or None
    else:
        payload["android_store_url"] = update_settings["store_url"] or None

    return JsonResponse(payload)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_dashboard(request):
    _ensure_admin_user(request.user)

    return Response(
        {
            "users_total": ApiUser.objects.count(),
            "users_active": ApiUser.objects.filter(is_active=True).count(),
            "users_blocked": ApiUser.objects.filter(
                is_active=False, blocked_at__isnull=False
            ).count(),
            "suppliers_total": ApiUser.objects.filter(role=ApiUser.ROLE_SUPPLIER).count(),
            "reports_open": UserReport.objects.filter(
                status=UserReport.STATUS_OPEN
            ).count(),
            "reports_total": UserReport.objects.count(),
            "requests_total": PartRequest.objects.count(),
            "conversations_total": Conversation.objects.count(),
            "messages_total": Message.objects.count(),
            "spare_parts_total": SparePart.objects.count(),
            "car_makes_total": CarMake.objects.count(),
            "car_models_total": CarModel.objects.count(),
            "subscriptions_active": Subscription.objects.filter(
                status=Subscription.STATUS_ACTIVE
            ).count(),
            "payments_pending": Payment.objects.filter(
                status=Payment.STATUS_PENDING
            ).count(),
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def privacy_policy_page(request):
    return render(request, "api/privacy_policy.html")


class ApiUserViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    queryset = ApiUser.objects.prefetch_related("car_model_links__car_model__make").order_by("id")
    serializer_class = ApiUserSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        search = str(self.request.query_params.get("search", "") or "").strip()
        role = str(self.request.query_params.get("role", "") or "").strip().lower()
        status_filter = str(
            self.request.query_params.get("status", "") or ""
        ).strip().lower()

        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(phone__icontains=search)
                | Q(city__icontains=search)
            )
        if role in {ApiUser.ROLE_USER, ApiUser.ROLE_SUPPLIER}:
            queryset = queryset.filter(role=role)
        if status_filter == "active":
            queryset = queryset.filter(is_active=True)
        elif status_filter == "online":
            queryset = queryset.filter(
                is_active=True,
                chat_last_seen_at__gte=timezone.now() - timedelta(minutes=5),
            )
        elif status_filter == "blocked":
            queryset = queryset.filter(is_active=False, blocked_at__isnull=False)

        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return PublicUserProfileSerializer
        return ApiUserSerializer

    @action(detail=True, methods=["post"], url_path="set-role")
    def set_role(self, request, pk=None):
        _ensure_admin_user(request.user)
        user = self.get_object()
        if user.pk == request.user.pk:
            raise ValidationError({"detail": "You cannot change your own role."})

        role = str(request.data.get("role", "") or "").strip().lower()
        if role not in {ApiUser.ROLE_USER, ApiUser.ROLE_SUPPLIER}:
            raise ValidationError({"role": "Choose user or supplier."})

        user.role = role
        user.save(update_fields=["role"])
        return Response(ApiUserSerializer(user, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        _ensure_admin_user(request.user)
        user = self.get_object()
        if user.pk == request.user.pk:
            raise ValidationError({"detail": "Use the normal password flow for your own account."})

        password = str(request.data.get("password", "") or "")
        if len(password) < 8:
            raise ValidationError({"password": "Password must be at least 8 characters."})

        user.set_password(password)
        user.save(update_fields=["password"])
        return Response(ApiUserSerializer(user, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], url_path="verify-phone")
    def verify_phone(self, request, pk=None):
        _ensure_admin_user(request.user)
        user = self.get_object()
        user.phone_verified_at = timezone.now()
        user.save(update_fields=["phone_verified_at"])
        return Response(ApiUserSerializer(user, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def block(self, request, pk=None):
        _ensure_admin_user(request.user)
        user = self.get_object()
        if user.pk == request.user.pk:
            raise ValidationError({"detail": "You cannot block your own account."})

        reason = str(request.data.get("reason", "") or "").strip()
        user.is_active = False
        user.blocked_at = timezone.now()
        user.blocked_reason = reason
        user.blocked_by = request.user
        user.save(
            update_fields=[
                "is_active",
                "blocked_at",
                "blocked_reason",
                "blocked_by",
            ]
        )
        return Response(ApiUserSerializer(user, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def unblock(self, request, pk=None):
        _ensure_admin_user(request.user)
        user = self.get_object()
        user.is_active = True
        user.blocked_at = None
        user.blocked_reason = ""
        user.blocked_by = None
        user.save(
            update_fields=[
                "is_active",
                "blocked_at",
                "blocked_reason",
                "blocked_by",
            ]
        )
        return Response(ApiUserSerializer(user, context=self.get_serializer_context()).data)

    @action(detail=False, methods=["delete", "post"], url_path="delete-by-phone")
    def delete_by_phone(self, request):
        _ensure_admin_user(request.user)

        phone = normalize_phone_number(request.data.get("phone"))
        try:
            user = ApiUser.objects.get(phone=phone)
        except ApiUser.DoesNotExist as exc:
            raise ValidationError({"phone": "No user found with this phone number."}) from exc

        if user.pk == request.user.pk:
            raise ValidationError({"detail": "You cannot delete your own account from this endpoint."})

        deleted_user_id = user.id
        delete_account(user)
        return Response(
            {
                "detail": "Account deleted successfully.",
                "deleted_user_id": deleted_user_id,
                "phone": phone,
            }
        )


class FirebaseRegistrationView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request):
        serializer = FirebaseRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        user_serializer = MeSerializer(user, context={"request": request})
        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": user_serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )


class RegistrationPhoneCheckThrottle(AnonRateThrottle):
    """Limit unauthenticated phone-existence checks to reduce enumeration abuse."""

    scope = "registration_phone_check"

    def get_rate(self):
        return "10/min"


class RegistrationPhoneCheckView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [RegistrationPhoneCheckThrottle]
    parser_classes = [JSONParser, FormParser]

    def post(self, request):
        try:
            phone = normalize_phone_number(request.data.get("phone"))
        except ValidationError as exc:
            raise ValidationError({"phone": exc.detail}) from exc

        return Response(
            {
                "available": not ApiUser.objects.filter(phone=phone).exists(),
            }
        )


class FirebasePasswordResetView(APIView):
    permission_classes = [AllowAny]
    parser_classes = [JSONParser, FormParser]

    def post(self, request):
        serializer = FirebasePasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Password updated successfully."})


class SparePartViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = SparePart.objects.order_by("id")
    serializer_class = SparePartSerializer

    def perform_create(self, serializer):
        _ensure_admin_user(self.request.user)
        serializer.save()

    def perform_update(self, serializer):
        _ensure_admin_user(self.request.user)
        serializer.save()

    def perform_destroy(self, instance):
        _ensure_admin_user(self.request.user)
        instance.delete()


class CarMakeViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [AllowAny]
    serializer_class = CarMakeSerializer

    def get_queryset(self):
        return CarMake.objects.prefetch_related("models").order_by("name")

    def create(self, request, *args, **kwargs):
        _ensure_admin_user(request.user)
        name = str(request.data.get("name", "") or "").strip()
        if not name:
            raise ValidationError({"name": "Name is required."})
        try:
            make = CarMake.objects.create(name=name)
        except IntegrityError as exc:
            raise ValidationError({"name": "This make already exists."}) from exc
        return Response(CarMakeSerializer(make, context={"request": request}).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        _ensure_admin_user(request.user)
        make = self.get_object()
        name = str(request.data.get("name", "") or "").strip()
        if not name:
            raise ValidationError({"name": "Name is required."})
        make.name = name
        make.save()
        return Response(CarMakeSerializer(make, context={"request": request}).data)

    partial_update = update

    def destroy(self, request, *args, **kwargs):
        _ensure_admin_user(request.user)
        self.get_object().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CarModelViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [AllowAny]
    serializer_class = CarModelSerializer

    def get_queryset(self):
        queryset = CarModel.objects.select_related("make")
        if not _is_admin_user(self.request.user):
            queryset = queryset.filter(is_active=True)
        make_id = str(self.request.query_params.get("make_id") or "").strip()
        make_slug = str(self.request.query_params.get("make") or "").strip().lower()
        search = str(
            self.request.query_params.get("search")
            or self.request.query_params.get("q")
            or ""
        ).strip()

        if make_id.isdigit():
            queryset = queryset.filter(make_id=int(make_id))
        elif make_slug:
            queryset = queryset.filter(make__slug=make_slug)

        if search:
            translated_search = translate_car_model_search_query(search)
            search_queries = [search]
            if translated_search.casefold() != search.casefold():
                search_queries.append(translated_search)

            query_filter = Q()
            starts_with_rank = Q()
            for search_query in search_queries:
                terms = [term for term in search_query.split() if term]
                if not terms:
                    continue

                term_filter = Q()
                for term in terms:
                    term_filter &= (
                        Q(name__icontains=term)
                        | Q(make__name__icontains=term)
                        | Q(slug__icontains=term)
                    )
                query_filter |= term_filter
                starts_with_rank |= (
                    Q(name__istartswith=search_query)
                    | Q(make__name__istartswith=search_query)
                    | Q(slug__istartswith=search_query)
                )

            queryset = queryset.filter(query_filter)
            queryset = queryset.annotate(
                match_rank=Case(
                    When(starts_with_rank, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            ).order_by("match_rank", "make__name", "name")
            return queryset

        return queryset.order_by("make__name", "name")

    def create(self, request, *args, **kwargs):
        _ensure_admin_user(request.user)
        name = str(request.data.get("name", "") or "").strip()
        make_id = request.data.get("make_id")
        if not name or not str(make_id or "").isdigit():
            raise ValidationError({"detail": "A name and make_id are required."})
        try:
            make = CarMake.objects.get(pk=int(make_id))
        except CarMake.DoesNotExist as exc:
            raise ValidationError({"make_id": "Car make does not exist."}) from exc
        model = CarModel(make=make, name=name)
        model.save()
        return Response(CarModelSerializer(model, context={"request": request}).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        _ensure_admin_user(request.user)
        model = self.get_object()
        name = str(request.data.get("name", "") or "").strip()
        if not name:
            raise ValidationError({"name": "Name is required."})
        model.name = name
        if str(request.data.get("make_id", "")).isdigit():
            try:
                model.make = CarMake.objects.get(pk=int(request.data["make_id"]))
            except CarMake.DoesNotExist as exc:
                raise ValidationError({"make_id": "Car make does not exist."}) from exc
        if "is_active" in request.data:
            model.is_active = bool(request.data["is_active"])
        model.save()
        return Response(CarModelSerializer(model, context={"request": request}).data)

    partial_update = update

    def destroy(self, request, *args, **kwargs):
        _ensure_admin_user(request.user)
        self.get_object().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PartRequestStatusViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = PartRequestStatus.objects.order_by("id")
    serializer_class = PartRequestStatusSerializer


class PartRequestViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = PartRequestSerializer
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _localize_response(self, response):
        localize_part_request_response_data(
            response.data,
            target_language=resolve_requested_translation_language(self.request),
        )
        return response

    def get_queryset(self):
        is_admin = _is_admin_user(self.request.user)
        qs = PartRequest.objects.select_related(
            "requester",
            "status",
            "car_model__make",
        )
        if not is_admin:
            qs = qs.filter(expires_at__gt=timezone.now())

        qs = qs.annotate(
            accepted_access_user_id=Subquery(
                PartRequestAccess.objects.filter(
                    part_request=OuterRef("pk"),
                    status=PartRequestAccess.STATUS_ACCEPTED,
                )
                .order_by("-updated_at", "-id")
                .values("user_id")[:1]
            )
        ).prefetch_related(
            "images",
            Prefetch(
                "access_requests",
                queryset=PartRequestAccess.objects.select_related(
                    "user",
                    "resolved_by",
                ).order_by("-requested_at", "-id"),
            ),
        ).order_by("-created_at")

        city = self.request.query_params.get("city")
        min_price = self.request.query_params.get("min_price")
        max_price = self.request.query_params.get("max_price")
        keyword = self.request.query_params.get("keyword")
        car_make_id = self.request.query_params.get("car_make_id")
        car_model_id = self.request.query_params.get("car_model_id")
        status_id = self.request.query_params.get("status_id")
        status_code = self.request.query_params.get("status_code")
        all_models = self.request.query_params.get("all_models")

        if city:
            qs = qs.filter(city__iexact=city)
        if min_price:
            qs = qs.filter(min_price__gte=min_price)
        if max_price:
            qs = qs.filter(max_price__lte=max_price)
        if keyword:
            qs = qs.filter(Q(title__icontains=keyword) | Q(description__icontains=keyword))
        if car_make_id:
            qs = qs.filter(car_model__make_id=car_make_id)
        if car_model_id:
            qs = qs.filter(car_model_id=car_model_id)
        if status_id:
            qs = qs.filter(status_id=status_id)
        if status_code:
            qs = qs.filter(status__code=status_code)

        user = getattr(self.request, "user", None)
        if is_admin:
            return qs

        if user is None or not user.is_authenticated:
            qs = qs.filter(accepted_access_user_id__isnull=True)
        else:
            qs = qs.filter(
                Q(accepted_access_user_id__isnull=True)
                | Q(requester_id=user.id)
                | Q(accepted_access_user_id=user.id)
            )

            # Suppliers browse only requests for car models they support.
            # Their own requests and requests already assigned to them remain
            # visible if their supported-model list changes later.
            if user.role == ApiUser.ROLE_SUPPLIER and all_models not in {
                "1",
                "true",
                "yes",
            }:
                qs = qs.filter(
                    Q(car_model__user_links__user_id=user.id)
                    | Q(requester_id=user.id)
                    | Q(accepted_access_user_id=user.id)
                ).distinct()

        return qs

    def _ensure_request_owner(self, part_request):
        if part_request.requester_id != self.request.user.id:
            raise PermissionDenied("You can only modify your own requests.")

    def _get_accepted_access_for_user(self, part_request, user):
        if user is None:
            return None
        return (
            PartRequestAccess.objects.select_related("conversation")
            .filter(
                part_request=part_request,
                user=user,
                status=PartRequestAccess.STATUS_ACCEPTED,
            )
            .first()
        )

    def _ensure_request_status_manager(self, part_request, validated_data):
        if _is_admin_user(self.request.user):
            return None
        if part_request.requester_id == self.request.user.id:
            return None

        access = self._get_accepted_access_for_user(part_request, self.request.user)
        if access is None:
            raise PermissionDenied("You can only modify your own requests.")

        editable_fields = set(validated_data.keys())
        if editable_fields - {"status"}:
            raise PermissionDenied(
                "You can only update the request status after access is approved."
            )
        return access

    def _notify_status_change_by_supplier(
        self,
        *,
        part_request,
        access,
        previous_status,
        next_status,
    ):
        if access is None or access.conversation_id is None:
            return
        if previous_status is None or next_status is None:
            return
        if previous_status.id == next_status.id:
            return

        create_and_broadcast_system_chat_message(
            conversation_id=access.conversation_id,
            sender=self.request.user,
            text=(
                f'Updated the status of "{part_request.title}" '
                f'from "{previous_status.label}" to "{next_status.label}".'
            ),
        )

    def _get_list_value(self, key):
        data = self.request.data
        if hasattr(data, "getlist"):
            return data.getlist(key)

        value = data.get(key)
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]

    def _delete_part_images(self, images):
        for image in images:
            if image.image:
                image.image.delete(save=False)
            if image.thumbnail:
                image.thumbnail.delete(save=False)
            image.delete()

    def _create_part_images(self, part_request):
        for image in self.request.FILES.getlist("images"):
            PartImage.objects.create(part_request=part_request, image=image)

    def _ensure_car_model_image(self, part_request):
        if not part_request.car_model_id:
            return

        car_model = getattr(part_request, "car_model", None)
        if car_model is None:
            car_model = CarModel.objects.select_related("make").filter(
                pk=part_request.car_model_id
            ).first()
            if car_model is None:
                return
            part_request.car_model = car_model

        try:
            _car_catalog_sync_service.ensure_model_image(car_model)
        except CarImagesApiError as exc:
            logger.warning(
                "Unable to populate image for car model %s while handling part request %s: %s",
                car_model.id,
                part_request.id,
                exc,
            )

    def _sync_part_images(self, part_request):
        sync_images = str(self.request.data.get("sync_images", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not sync_images:
            return

        keep_image_ids = {
            int(image_id)
            for image_id in self._get_list_value("keep_image_ids")
            if str(image_id).strip().isdigit()
        }

        removed_images = part_request.images.exclude(id__in=keep_image_ids)
        self._delete_part_images(list(removed_images))
        self._create_part_images(part_request)

    def perform_create(self, serializer):
        part_request = serializer.save(requester=self.request.user)
        self._ensure_car_model_image(part_request)
        self._create_part_images(part_request)
        send_request_created_push_notifications(part_request)

    def perform_update(self, serializer):
        previous_status = serializer.instance.status
        access = self._ensure_request_status_manager(
            serializer.instance,
            serializer.validated_data,
        )
        part_request = serializer.save()
        self._ensure_car_model_image(part_request)
        if part_request.requester_id == self.request.user.id:
            self._sync_part_images(part_request)
            return

        self._notify_status_change_by_supplier(
            part_request=part_request,
            access=access,
            previous_status=previous_status,
            next_status=part_request.status,
        )

    def perform_destroy(self, instance):
        if _is_admin_user(self.request.user):
            self._delete_part_images(list(instance.images.all()))
            instance.delete()
            return
        self._ensure_request_owner(instance)
        self._delete_part_images(list(instance.images.all()))
        instance.delete()

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return self._localize_response(response)

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        return self._localize_response(response)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        return self._localize_response(response)

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        return self._localize_response(response)

    def partial_update(self, request, *args, **kwargs):
        response = super().partial_update(request, *args, **kwargs)
        return self._localize_response(response)


class PartImageViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = PartImage.objects.order_by("-created_at")
    serializer_class = PartImageSerializer


class PartRequestAccessViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class = PartRequestAccessSerializer

    def get_queryset(self):
        is_admin = _is_admin_user(self.request.user)
        queryset = (
            PartRequestAccess.objects.select_related(
                "part_request",
                "part_request__status",
                "part_request__car_model__make",
                "user",
                "resolved_by",
                "conversation",
            )
            .filter(
                Q(user=self.request.user) | Q(part_request__requester=self.request.user)
            )
            .filter(part_request__expires_at__gt=timezone.now())
            .order_by("-requested_at", "-id")
        )
        if is_admin:
            queryset = queryset.model.objects.select_related(
                "part_request",
                "part_request__status",
                "part_request__car_model__make",
                "user",
                "resolved_by",
                "conversation",
            ).order_by("-requested_at", "-id")

        part_request_id = self.request.query_params.get("part_request")
        conversation_id = self.request.query_params.get("conversation")
        status_value = self.request.query_params.get("status")
        role = str(self.request.query_params.get("role", "") or "").strip().lower()

        if part_request_id:
            queryset = queryset.filter(part_request_id=part_request_id)
        if conversation_id:
            queryset = queryset.filter(conversation_id=conversation_id)
        if status_value:
            queryset = queryset.filter(status=status_value)
        if role == "mine":
            queryset = queryset.filter(user=self.request.user)
        elif role == "incoming":
            queryset = queryset.filter(part_request__requester=self.request.user)
        elif role == "granted":
            queryset = queryset.filter(
                user=self.request.user,
                status=PartRequestAccess.STATUS_ACCEPTED,
            )

        return queryset

    def _ensure_participants_share_conversation(self, conversation, part_request):
        participant_ids = set(
            ConversationParticipant.objects.filter(conversation=conversation)
            .values_list("user_id", flat=True)
            .distinct()
        )
        required_ids = {self.request.user.id, part_request.requester_id}
        if not required_ids.issubset(participant_ids):
            raise ValidationError(
                {
                    "conversation": (
                        "The conversation must include both the request owner "
                        "and the current user."
                    )
                }
            )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        part_request = serializer.validated_data["part_request"]
        if part_request.is_expired:
            raise ValidationError(
                {"part_request": "This request has expired and is no longer available."}
            )
        conversation = serializer.validated_data.get("conversation")
        if conversation is None:
            raise ValidationError({"conversation": "conversation is required."})
        if part_request.requester_id == request.user.id:
            raise ValidationError(
                {"part_request": "You already own this request."}
            )

        self._ensure_participants_share_conversation(conversation, part_request)

        existing_access = (
            PartRequestAccess.objects.filter(
                part_request=part_request,
                user=request.user,
            )
            .select_related(
                "part_request",
                "part_request__status",
                "part_request__car_model__make",
                "user",
                "resolved_by",
                "conversation",
            )
            .first()
        )

        created = False
        if existing_access is None:
            access = PartRequestAccess.objects.create(
                part_request=part_request,
                user=request.user,
                conversation=conversation,
                status=PartRequestAccess.STATUS_PENDING,
            )
            created = True
        else:
            access = existing_access
            if access.status == PartRequestAccess.STATUS_ACCEPTED:
                raise ValidationError(
                    {"detail": "You already have access to manage this request."}
                )
            if access.status == PartRequestAccess.STATUS_PENDING:
                raise ValidationError(
                    {
                        "detail": (
                            "You already have a pending access request for this part request."
                        )
                    }
                )

            access.conversation = conversation
            access.status = PartRequestAccess.STATUS_PENDING
            access.resolved_by = None
            access.resolved_at = None
            access.save(
                update_fields=[
                    "conversation",
                    "status",
                    "resolved_by",
                    "resolved_at",
                    "updated_at",
                ]
            )

        access.refresh_from_db()
        create_and_broadcast_system_chat_message(
            conversation_id=conversation.id,
            sender=request.user,
            text=f'Requested access to manage the status of "{part_request.title}".',
        )

        response_serializer = self.get_serializer(access)
        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        headers = self.get_success_headers(response_serializer.data) if created else {}
        return Response(response_serializer.data, status=response_status, headers=headers)

    def _ensure_owner_can_decide(self, access):
        if _is_admin_user(self.request.user):
            return
        if access.part_request.requester_id != self.request.user.id:
            raise PermissionDenied("Only the request owner can review access requests.")

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        access = self.get_object()
        self._ensure_owner_can_decide(access)
        if access.status != PartRequestAccess.STATUS_PENDING:
            raise ValidationError(
                {"detail": "Only pending access requests can be approved."}
            )

        decided_at = timezone.now()
        with transaction.atomic():
            (
                PartRequestAccess.objects.filter(
                    part_request=access.part_request,
                    status=PartRequestAccess.STATUS_ACCEPTED,
                )
                .exclude(pk=access.pk)
                .update(
                    status=PartRequestAccess.STATUS_REVOKED,
                    resolved_by=request.user,
                    resolved_at=decided_at,
                    updated_at=decided_at,
                )
            )
            (
                PartRequestAccess.objects.filter(
                    part_request=access.part_request,
                    status=PartRequestAccess.STATUS_PENDING,
                )
                .exclude(pk=access.pk)
                .update(
                    status=PartRequestAccess.STATUS_REJECTED,
                    resolved_by=request.user,
                    resolved_at=decided_at,
                    updated_at=decided_at,
                )
            )
            access.status = PartRequestAccess.STATUS_ACCEPTED
            access.resolved_by = request.user
            access.resolved_at = decided_at
            access.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])

        access.refresh_from_db()
        if access.conversation_id:
            create_and_broadcast_system_chat_message(
                conversation_id=access.conversation_id,
                sender=request.user,
                text=(
                    f'Approved access to manage the status of "{access.part_request.title}".'
                ),
            )

        return Response(self.get_serializer(access).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        access = self.get_object()
        self._ensure_owner_can_decide(access)
        if access.status != PartRequestAccess.STATUS_PENDING:
            raise ValidationError(
                {"detail": "Only pending access requests can be rejected."}
            )

        access.status = PartRequestAccess.STATUS_REJECTED
        access.resolved_by = request.user
        access.resolved_at = timezone.now()
        access.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])
        access.refresh_from_db()

        if access.conversation_id:
            create_and_broadcast_system_chat_message(
                conversation_id=access.conversation_id,
                sender=request.user,
                text=(
                    f'Rejected access to manage the status of "{access.part_request.title}".'
                ),
            )

        return Response(self.get_serializer(access).data)


class ConversationViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]

    def _localize_response(self, response):
        localize_conversation_response_data(
            response.data,
            target_language=resolve_requested_translation_language(self.request),
        )
        return response

    def get_queryset(self):
        user = self.request.user
        epoch = timezone.make_aware(datetime(1970, 1, 1))
        can_view_all_chats = _can_view_all_chats(user)

        last_read_subquery = ConversationParticipant.objects.filter(
            conversation=OuterRef("pk"), user=user
        ).values("last_read_at")[:1]

        last_message_subquery = Message.objects.filter(conversation=OuterRef("pk"))
        if not can_view_all_chats:
            last_message_subquery = last_message_subquery.exclude(
                hidden_for_users__user=user
            )
        last_message_subquery = last_message_subquery.annotate(
            activity_at=Coalesce("server_timestamp", "client_timestamp"),
        ).order_by("-activity_at", "-server_timestamp", "-id", "-client_timestamp")

        qs = (
            Conversation.objects.all()
            if can_view_all_chats
            else Conversation.objects.filter(participants__user=user)
        )
        qs = (
            qs
            .annotate(
                last_read_at=Subquery(last_read_subquery),
                last_read_at_coalesced=Coalesce(
                    Subquery(last_read_subquery), Value(epoch)
                ),
                latest_message_id=Subquery(last_message_subquery.values("id")[:1]),
                latest_message_text=Subquery(last_message_subquery.values("text")[:1]),
                latest_message_sender_id=Subquery(last_message_subquery.values("sender_id")[:1]),
                latest_message_sender_name=Subquery(
                    last_message_subquery.values("sender__name")[:1]
                ),
                latest_message_timestamp=Subquery(
                    last_message_subquery.values("activity_at")[:1]
                ),
                latest_message_edited_at=Subquery(last_message_subquery.values("edited_at")[:1]),
                latest_message_is_deleted=Subquery(
                    last_message_subquery.values("is_deleted")[:1]
                ),
                latest_activity_at=Coalesce(
                    Subquery(last_message_subquery.values("activity_at")[:1]),
                    F("created_at"),
                ),
            )
            .annotate(
                unread_count=Count(
                    "messages",
                    filter=Q(messages__server_timestamp__gt=F("last_read_at_coalesced"))
                    & ~Q(messages__sender=user)
                    & (
                        ~Q(messages__hidden_for_users__user=user)
                        if not can_view_all_chats
                        else Q()
                    ),
                    distinct=True,
                )
            )
            .prefetch_related(
                Prefetch(
                    "participants",
                    queryset=ConversationParticipant.objects.select_related("user"),
                )
            )
            .order_by("-latest_activity_at", "-latest_message_id", "-id")
        )
        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return ConversationListSerializer
        return ConversationSerializer

    def perform_create(self, serializer):
        conversation = serializer.save()
        ConversationParticipant.objects.get_or_create(
            conversation=conversation, user=self.request.user
        )

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return self._localize_response(response)


class ConversationParticipantViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class = ConversationParticipantSerializer

    def get_queryset(self):
        return (
            ConversationParticipant.objects.filter(
                conversation__participants__user=self.request.user
            )
            .select_related("conversation", "user")
            .order_by("-joined_at")
            .distinct()
        )

    def perform_create(self, serializer):
        conversation = serializer.validated_data["conversation"]
        if not ConversationParticipant.objects.filter(
            conversation=conversation, user=self.request.user
        ).exists():
            raise PermissionDenied("You are not a participant in this conversation.")
        serializer.save()


class MessageViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    pagination_class = MessageCursorPagination
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _localize_payload(self, payload):
        localize_message_response_data(
            payload,
            target_language=resolve_requested_translation_language(self.request),
        )
        return payload

    def get_serializer_class(self):
        if self.action == "list":
            return MessageListSerializer
        return MessageCreateSerializer

    def _get_conversation(self):
        conversation_id = self.request.query_params.get("conversation_id")
        if self.action != "list":
            conversation_id = self.request.data.get("conversation")
        if not conversation_id:
            raise ValidationError({"conversation_id": "conversation_id is required."})
        try:
            return Conversation.objects.get(pk=conversation_id)
        except Conversation.DoesNotExist as exc:
            raise ValidationError({"conversation_id": "Conversation not found."}) from exc

    def _ensure_participant(self, conversation):
        exists = ConversationParticipant.objects.filter(
            conversation=conversation, user=self.request.user
        ).exists()
        if not exists:
            raise PermissionDenied("You are not a participant in this conversation.")

    def get_queryset(self):
        queryset = Message.objects.all()
        can_view_all_chats = _can_view_all_chats(self.request.user)
        if not can_view_all_chats:
            queryset = queryset.filter(
                conversation__participants__user=self.request.user
            )
        queryset = queryset.select_related(
            "sender",
            "product",
            "product__status",
            "product__car_model__make",
            "reply_to__sender",
            "reply_to__product",
            "reply_to__product__status",
            "reply_to__product__car_model__make",
        )
        queryset = queryset.prefetch_related(
            "attachments",
            "statuses__message",
            "statuses",
            "reply_to__hidden_for_users",
        ).distinct()

        if self.action == "list":
            conversation_id = self.request.query_params.get("conversation_id")
            if not conversation_id:
                raise ValidationError({"conversation_id": "conversation_id is required."})

            conversation = self._get_conversation()
            if not can_view_all_chats:
                self._ensure_participant(conversation)
            queryset = queryset.filter(conversation=conversation).exclude(
                hidden_for_users__user=self.request.user
            ) if not can_view_all_chats else queryset.filter(
                conversation=conversation
            )

        return queryset.order_by("client_timestamp", "server_timestamp", "id")

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        localize_message_response_data(
            response.data,
            target_language=resolve_requested_translation_language(request),
        )
        return response

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = self._get_conversation()
        self._ensure_participant(conversation)

        reply_to = serializer.validated_data.get("reply_to")
        if reply_to and reply_to.conversation_id != conversation.id:
            raise ValidationError({"reply_to": "reply_to must be in the same conversation."})

        files = request.FILES.getlist("files") or []
        if not files and "file" in request.FILES:
            files = [request.FILES["file"]]

        if serializer.validated_data.get("message_type") == "media" and not files:
            raise ValidationError({"files": "Media message requires file(s)."})

        delivered_user_ids = get_default_delivered_user_ids(conversation.id) - {
            request.user.id
        }

        try:
            payload, status_events = create_message_with_statuses(
                conversation_id=conversation.id,
                sender=request.user,
                text=serializer.validated_data.get("text", ""),
                message_type=serializer.validated_data.get("message_type", "text"),
                client_timestamp=serializer.validated_data["client_timestamp"],
                product=serializer.validated_data.get("product"),
                reply_to=reply_to,
                files=files,
                delivered_user_ids=delivered_user_ids,
                base_url=request.build_absolute_uri("/"),
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        broadcast_created_message(payload, status_events)
        broadcast_inbox_message(payload)
        send_chat_message_push_notifications(payload)
        self._localize_payload(payload)
        headers = self.get_success_headers({"id": payload["id"]})
        return Response(payload, status=201, headers=headers)

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        message = self.get_object()
        if message.sender_id != request.user.id:
            raise PermissionDenied("You can only edit your own messages.")

        previous_text = message.text
        new_text = str(request.data.get("text", "") or "").strip()

        try:
            payload = update_text_message(message, text=new_text)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        if previous_text != new_text:
            broadcast_chat_event(
                message.conversation_id,
                "message_created",
                {"message": payload},
            )
            broadcast_inbox_message(payload)

        self._localize_payload(payload)
        return Response(payload)

    def destroy(self, request, *args, **kwargs):
        message = self.get_object()
        scope = str(request.query_params.get("scope", "") or "").strip().lower()
        if scope not in {"all", "me"}:
            raise ValidationError({"scope": "scope must be either 'all' or 'me'."})

        if scope == "all":
            if (
                not _can_view_all_chats(request.user)
                and message.sender_id != request.user.id
            ):
                raise PermissionDenied(
                    "You can only delete your own messages for everyone."
                )
            payload = delete_message_for_everyone(message)
            broadcast_chat_event(
                message.conversation_id,
                "message_created",
                {"message": payload},
            )
            broadcast_inbox_message(payload)
            self._localize_payload(payload)
            return Response(
                {
                    "scope": "all",
                    "message_id": int(message.id),
                    "conversation_id": int(message.conversation_id),
                    "message": payload,
                }
            )

        hide_message_for_user(message, request.user)
        return Response(
            {
                "scope": "me",
                "message_id": int(message.id),
                "conversation_id": int(message.conversation_id),
            }
        )


class MessageStatusViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class = MessageStatusSerializer

    def get_queryset(self):
        queryset = MessageStatus.objects.select_related("message", "user").filter(
            message__conversation__participants__user=self.request.user
        ).exclude(
            message__hidden_for_users__user=self.request.user
        )
        conversation_id = self.request.query_params.get("conversation_id")
        if conversation_id:
            queryset = queryset.filter(message__conversation_id=conversation_id)

        return queryset.order_by("-updated_at").distinct()

    def perform_create(self, serializer):
        message = serializer.validated_data["message"]
        if not ConversationParticipant.objects.filter(
            conversation_id=message.conversation_id,
            user=self.request.user,
        ).exists():
            raise PermissionDenied("You are not a participant in this conversation.")
        instance = serializer.save(user=self.request.user)
        status_event = {
            "conversation_id": int(message.conversation_id),
            "message_id": int(message.id),
            "user_id": int(instance.user_id),
            "status": instance.status,
            "updated_at": instance.updated_at.isoformat(),
        }
        broadcast_chat_event(
            message.conversation_id,
            "message_status",
            status_event,
        )


class UserReportViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "create":
            return UserReportCreateSerializer
        if self.action in {"update", "partial_update"}:
            return UserReportAdminUpdateSerializer
        return UserReportSerializer

    def get_queryset(self):
        queryset = UserReport.objects.select_related(
            "reporter",
            "reported_user",
            "reviewed_by",
        )
        if not _is_admin_user(self.request.user):
            queryset = queryset.filter(reporter=self.request.user)

        reported_user_id = self.request.query_params.get("reported_user")
        reporter_id = self.request.query_params.get("reporter")
        report_status = self.request.query_params.get("status")

        if reported_user_id:
            queryset = queryset.filter(reported_user_id=reported_user_id)
        if reporter_id:
            queryset = queryset.filter(reporter_id=reporter_id)
        if report_status:
            queryset = queryset.filter(status=report_status)

        return queryset.order_by("-created_at", "-id")

    def perform_create(self, serializer):
        serializer.save(reporter=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report = serializer.save(reporter=request.user)
        response_serializer = UserReportSerializer(
            report,
            context=self.get_serializer_context(),
        )
        headers = self.get_success_headers(response_serializer.data)
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        _ensure_admin_user(request.user)
        report = self.get_object()
        serializer = self.get_serializer(
            report,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        next_status = serializer.validated_data.get("status", report.status)
        admin_notes = serializer.validated_data.get("admin_notes", report.admin_notes)

        report.status = next_status
        report.admin_notes = admin_notes
        report.reviewed_by = request.user
        report.reviewed_at = timezone.now()
        report.save(
            update_fields=[
                "status",
                "admin_notes",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )
        response_serializer = UserReportSerializer(
            report,
            context=self.get_serializer_context(),
        )
        return Response(response_serializer.data)


class MobileDeviceViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class = MobileDeviceSerializer

    def get_queryset(self):
        return MobileDevice.objects.filter(user=self.request.user).order_by(
            "-last_seen_at", "-updated_at", "-id"
        )

    def create(self, request, *args, **kwargs):
        device_id = str(request.data.get("device_id", "")).strip()
        instance = None
        if device_id:
            instance = MobileDevice.objects.filter(
                user=request.user,
                device_id=device_id,
            ).first()

        serializer = self.get_serializer(instance=instance, data=request.data, partial=bool(instance))
        serializer.is_valid(raise_exception=True)
        device = serializer.save(user=request.user)
        logger.info(
            "Registered mobile device for user %s: device_id=%s platform=%s "
            "is_active=%s has_token=%s.",
            request.user.id,
            device.device_id,
            device.platform,
            device.is_active,
            bool((device.push_token or "").strip()),
        )

        response_serializer = self.get_serializer(device)
        response_status = status.HTTP_200_OK if instance else status.HTTP_201_CREATED
        headers = self.get_success_headers(response_serializer.data) if not instance else {}
        return Response(response_serializer.data, status=response_status, headers=headers)

    @action(detail=False, methods=["post"], url_path="test-request-notification")
    def test_request_notification(self, request):
        queryset = self.get_queryset().exclude(push_token="")
        mobile_device_id = request.data.get("mobile_device_id")
        raw_device_id = str(request.data.get("device_id", "") or "").strip()

        if mobile_device_id not in (None, ""):
            device = queryset.filter(pk=mobile_device_id).first()
        elif raw_device_id:
            device = queryset.filter(device_id=raw_device_id).first()
        else:
            device = queryset.filter(is_active=True).first() or queryset.first()

        if device is None:
            raise ValidationError(
                {
                    "device_id": (
                        "No matching active device with a push token was found "
                        "for the current user."
                    )
                }
            )

        request_id = request.data.get("request_id")
        source_request = None
        if request_id not in (None, ""):
            source_request = (
                PartRequest.objects.select_related("requester")
                .filter(pk=request_id)
                .first()
            )
            if source_request is None:
                raise ValidationError({"request_id": "Part request not found."})
        else:
            source_request = (
                PartRequest.objects.select_related("requester")
                .order_by("-created_at", "-id")
                .first()
            )
            if source_request is None:
                raise ValidationError(
                    {
                        "request_id": (
                            "request_id is required because there are no part "
                            "requests available to attach to the test notification."
                        )
                    }
                )

        request_title = (
            str(request.data.get("request_title", "") or "").strip()
            or source_request.title
            or "Test seller request"
        )
        request_description = (
            str(request.data.get("request_description", "") or "").strip()
            or source_request.description
            or "Testing request-created push notification delivery."
        )
        seller_name = (
            str(request.data.get("seller_name", "") or "").strip()
            or str(getattr(source_request.requester, "name", "") or "").strip()
            or "Supplier"
        )

        result = send_test_request_notification(
            device=device,
            request_id=source_request.id,
            requester_id=source_request.requester_id,
            request_title=request_title,
            request_description=request_description,
            seller_name=seller_name,
            server_timestamp=timezone.now(),
        )

        return Response(
            {
                "overall_status": result.get("status"),
                "request_id": source_request.id,
                "request_title": request_title,
                "request_description": request_description,
                "seller_name": seller_name,
                "device": MobileDeviceSerializer(device).data,
                "result": result,
            }
        )


class MeView(RetrieveUpdateDestroyAPIView):
    serializer_class = MeSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def get_object(self):
        return self.request.user

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        delete_account(user)
        return Response(status=status.HTTP_204_NO_CONTENT)
