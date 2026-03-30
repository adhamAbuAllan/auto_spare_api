import logging
from datetime import datetime

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models import Count, F, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import (
    ApiUser,
    Conversation,
    ConversationParticipant,
    Message,
    MessageAttachment,
    MessageStatus,
    MobileDevice,
    PartImage,
    PartRequest,
    PartRequestStatus,
    SparePart,
)
from .pagination import MessageCursorPagination
from .serializers import (
    ApiUserSerializer,
    ConversationListSerializer,
    ConversationParticipantSerializer,
    ConversationSerializer,
    MeSerializer,
    MobileDeviceSerializer,
    MessageCreateSerializer,
    MessageListSerializer,
    MessageStatusSerializer,
    PartImageSerializer,
    PartRequestSerializer,
    PartRequestStatusSerializer,
    SparePartSerializer,
)
from chat.services import create_message_with_statuses, get_default_delivered_user_ids


logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return JsonResponse({"status": "ok"})


def _chat_group_name(conversation_id):
    return f"chat_{int(conversation_id)}"


def _broadcast_chat_event(conversation_id, event_type, payload):
    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    try:
        async_to_sync(channel_layer.group_send)(
            _chat_group_name(conversation_id),
            {
                "type": event_type,
                **payload,
            },
        )
    except Exception as exc:
        logger.warning(
            "Unable to broadcast chat event %s for conversation %s: %s",
            event_type,
            conversation_id,
            exc,
        )


def _broadcast_created_message(message_payload, status_events):
    _broadcast_chat_event(
        message_payload["conversation_id"],
        "message_created",
        {"message": message_payload},
    )
    for status_event in status_events:
        _broadcast_chat_event(
            status_event["conversation_id"],
            "message_status",
            status_event,
        )


class ApiUserViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = ApiUser.objects.order_by("id")
    serializer_class = ApiUserSerializer

    def get_permissions(self):
        if self.action == "create":
            return [AllowAny()]
        return [IsAuthenticated()]


class SparePartViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = SparePart.objects.order_by("id")
    serializer_class = SparePartSerializer


class PartRequestStatusViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = PartRequestStatus.objects.order_by("id")
    serializer_class = PartRequestStatusSerializer


class PartRequestViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = PartRequestSerializer

    def get_queryset(self):
        qs = (
            PartRequest.objects.select_related("requester", "status")
            .prefetch_related("images")
            .order_by("-created_at")
        )

        city = self.request.query_params.get("city")
        min_price = self.request.query_params.get("min_price")
        max_price = self.request.query_params.get("max_price")
        keyword = self.request.query_params.get("keyword")

        if city:
            qs = qs.filter(city__iexact=city)
        if min_price:
            qs = qs.filter(min_price__gte=min_price)
        if max_price:
            qs = qs.filter(max_price__lte=max_price)
        if keyword:
            qs = qs.filter(Q(title__icontains=keyword) | Q(description__icontains=keyword))

        return qs


class PartImageViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = PartImage.objects.order_by("-created_at")
    serializer_class = PartImageSerializer


class ConversationViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        epoch = timezone.make_aware(datetime(1970, 1, 1))

        last_read_subquery = ConversationParticipant.objects.filter(
            conversation=OuterRef("pk"), user=user
        ).values("last_read_at")[:1]

        last_message_subquery = (
            Message.objects.filter(conversation=OuterRef("pk"))
            .order_by("-client_timestamp", "-server_timestamp", "-id")
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
                latest_message_server_timestamp=Subquery(
                    last_message_subquery.values("server_timestamp")[:1]
                ),
            )
            .annotate(
                unread_count=Count(
                    "messages",
                    filter=Q(messages__server_timestamp__gt=F("last_read_at_coalesced"))
                    & ~Q(messages__sender=user),
                    distinct=True,
                )
            )
            .prefetch_related(
                Prefetch(
                    "participants",
                    queryset=ConversationParticipant.objects.select_related("user"),
                )
            )
            .order_by("-created_at")
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
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    pagination_class = MessageCursorPagination
    parser_classes = [JSONParser, FormParser, MultiPartParser]

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
        conversation_id = self.request.query_params.get("conversation_id")
        if not conversation_id:
            raise ValidationError({"conversation_id": "conversation_id is required."})

        conversation = self._get_conversation()
        self._ensure_participant(conversation)

        return (
            Message.objects.filter(conversation=conversation)
            .select_related("sender", "product", "reply_to__sender", "reply_to__product")
            .prefetch_related("attachments", "statuses__message", "statuses")
            .order_by("client_timestamp", "server_timestamp", "id")
        )

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

        _broadcast_created_message(payload, status_events)
        headers = self.get_success_headers({"id": payload["id"]})
        return Response(payload, status=201, headers=headers)


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
        _broadcast_chat_event(
            message.conversation_id,
            "message_status",
            {
                "conversation_id": int(message.conversation_id),
                "message_id": int(message.id),
                "user_id": int(instance.user_id),
                "status": instance.status,
                "updated_at": instance.updated_at.isoformat(),
            },
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

        response_serializer = self.get_serializer(device)
        response_status = status.HTTP_200_OK if instance else status.HTTP_201_CREATED
        headers = self.get_success_headers(response_serializer.data) if not instance else {}
        return Response(response_serializer.data, status=response_status, headers=headers)


class MeView(RetrieveUpdateAPIView):
    serializer_class = MeSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user
