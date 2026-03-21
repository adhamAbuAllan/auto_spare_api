from datetime import datetime

from django.db.models import Count, F, OuterRef, Prefetch, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated

from .models import (
    ApiUser,
    Conversation,
    ConversationParticipant,
    Message,
    MessageAttachment,
    MessageStatus,
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
    MessageCreateSerializer,
    MessageListSerializer,
    MessageStatusSerializer,
    PartImageSerializer,
    PartRequestSerializer,
    PartRequestStatusSerializer,
    SparePartSerializer,
)


@api_view(["GET"])
def health(request):
    return JsonResponse({"status": "ok"})


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
                last_message_id=Subquery(last_message_subquery.values("id")[:1]),
                last_message_text=Subquery(last_message_subquery.values("text")[:1]),
                last_message_sender_id=Subquery(last_message_subquery.values("sender_id")[:1]),
                last_message_sender_name=Subquery(
                    last_message_subquery.values("sender__name")[:1]
                ),
                last_message_server_timestamp=Subquery(
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
    queryset = ConversationParticipant.objects.order_by("-joined_at")
    serializer_class = ConversationParticipantSerializer


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
            .select_related("sender", "reply_to", "product")
            .prefetch_related("attachments")
            .order_by("client_timestamp", "server_timestamp", "id")
        )

    def perform_create(self, serializer):
        conversation = self._get_conversation()
        self._ensure_participant(conversation)

        reply_to = serializer.validated_data.get("reply_to")
        if reply_to and reply_to.conversation_id != conversation.id:
            raise ValidationError({"reply_to": "reply_to must be in the same conversation."})

        message = serializer.save(conversation=conversation, sender=self.request.user)

        files = self.request.FILES.getlist("files") or []
        if not files and "file" in self.request.FILES:
            files = [self.request.FILES["file"]]

        if message.message_type == "media" and not files:
            raise ValidationError({"files": "Media message requires file(s)."})

        for uploaded in files:
            MessageAttachment.objects.create(
                message=message,
                file=uploaded,
                content_type=getattr(uploaded, "content_type", ""),
                size=getattr(uploaded, "size", 0) or 0,
            )


class MessageStatusViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = MessageStatusSerializer

    def get_queryset(self):
        return MessageStatus.objects.select_related("message", "user").order_by(
            "-updated_at"
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class MeView(RetrieveUpdateAPIView):
    serializer_class = MeSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user
