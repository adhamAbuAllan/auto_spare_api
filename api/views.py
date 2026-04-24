import logging
from datetime import datetime
from django.db import transaction
from django.db.models import Count, F, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import (
    ApiUser,
    CarMake,
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
)
from .pagination import MessageCursorPagination
from .serializers import (
    ApiUserSerializer,
    CarMakeSerializer,
    ConversationListSerializer,
    ConversationParticipantSerializer,
    ConversationSerializer,
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
)
from .translation import (
    localize_conversation_response_data,
    localize_message_response_data,
    localize_part_request_response_data,
    resolve_requested_translation_language,
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


logger = logging.getLogger(__name__)


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


class ApiUserViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = ApiUser.objects.prefetch_related("car_model_links__car_model__make").order_by("id")
    serializer_class = ApiUserSerializer

    def get_permissions(self):
        if self.action == "create":
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return PublicUserProfileSerializer
        return ApiUserSerializer


class SparePartViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = SparePart.objects.order_by("id")
    serializer_class = SparePartSerializer


class CarMakeViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [AllowAny]
    serializer_class = CarMakeSerializer

    def get_queryset(self):
        return CarMake.objects.prefetch_related("models").order_by("name")


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
        qs = (
            PartRequest.objects.select_related("requester", "status", "car_model__make")
            .prefetch_related(
                "images",
                Prefetch(
                    "access_requests",
                    queryset=PartRequestAccess.objects.select_related(
                        "user",
                        "resolved_by",
                    ).order_by("-requested_at", "-id"),
                ),
            )
            .order_by("-created_at")
        )

        city = self.request.query_params.get("city")
        min_price = self.request.query_params.get("min_price")
        max_price = self.request.query_params.get("max_price")
        keyword = self.request.query_params.get("keyword")
        car_make_id = self.request.query_params.get("car_make_id")
        car_model_id = self.request.query_params.get("car_model_id")
        status_id = self.request.query_params.get("status_id")
        status_code = self.request.query_params.get("status_code")

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
            image.delete()

    def _create_part_images(self, part_request):
        for image in self.request.FILES.getlist("images"):
            PartImage.objects.create(part_request=part_request, image=image)

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
        self._create_part_images(part_request)
        send_request_created_push_notifications(part_request)

    def perform_update(self, serializer):
        previous_status = serializer.instance.status
        access = self._ensure_request_status_manager(
            serializer.instance,
            serializer.validated_data,
        )
        part_request = serializer.save()
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
            .order_by("-requested_at", "-id")
        )

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

        last_read_subquery = ConversationParticipant.objects.filter(
            conversation=OuterRef("pk"), user=user
        ).values("last_read_at")[:1]

        last_message_subquery = (
            Message.objects.filter(conversation=OuterRef("pk"))
            .exclude(hidden_for_users__user=user)
            .annotate(
                activity_at=Coalesce("server_timestamp", "client_timestamp"),
            )
            .order_by("-activity_at", "-server_timestamp", "-id", "-client_timestamp")
        )

        qs = (
            Conversation.objects.filter(participants__user=user)
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
                    & ~Q(messages__hidden_for_users__user=user),
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
        queryset = (
            Message.objects.filter(conversation__participants__user=self.request.user)
            .select_related(
                "sender",
                "product",
                "product__status",
                "product__car_model__make",
                "reply_to__sender",
                "reply_to__product",
                "reply_to__product__status",
                "reply_to__product__car_model__make",
            )
            .prefetch_related(
                "attachments",
                "statuses__message",
                "statuses",
                "reply_to__hidden_for_users",
            )
            .distinct()
        )

        if self.action == "list":
            conversation_id = self.request.query_params.get("conversation_id")
            if not conversation_id:
                raise ValidationError({"conversation_id": "conversation_id is required."})

            conversation = self._get_conversation()
            self._ensure_participant(conversation)
            queryset = queryset.filter(conversation=conversation).exclude(
                hidden_for_users__user=self.request.user
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
            if message.sender_id != request.user.id:
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
        files_to_delete = self._collect_owned_file_references(user)

        with transaction.atomic():
            user.delete()

        self._delete_files(files_to_delete)
        logger.info("Deleted account for user %s.", user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _collect_owned_file_references(self, user):
        files = []
        seen_names = set()

        def remember(file_field):
            if not file_field:
                return
            name = str(getattr(file_field, "name", "") or "").strip()
            if not name or name in seen_names:
                return
            seen_names.add(name)
            files.append((file_field.storage, name))

        remember(user.avatar)

        for image in PartImage.objects.filter(part_request__requester=user).iterator():
            remember(image.image)

        for attachment in MessageAttachment.objects.filter(message__sender=user).iterator():
            remember(attachment.file)

        return files

    def _delete_files(self, files):
        for storage, name in files:
            try:
                storage.delete(name)
            except Exception as exc:  # pragma: no cover - cleanup best effort
                logger.warning("Unable to delete uploaded file %s during account removal: %s", name, exc)
