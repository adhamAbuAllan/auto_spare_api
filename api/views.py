from django.http import JsonResponse
from rest_framework import mixins, viewsets
from rest_framework.decorators import api_view

from .models import (
    ApiUser,
    Conversation,
    ConversationParticipant,
    Message,
    MessageStatus,
    PartImage,
    PartRequest,
    PartRequestStatus,
    SparePart,
)
from .serializers import (
    ApiUserSerializer,
    ConversationParticipantSerializer,
    ConversationSerializer,
    MessageSerializer,
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
    queryset = PartRequest.objects.order_by("-created_at")
    serializer_class = PartRequestSerializer


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
    queryset = Conversation.objects.order_by("-created_at")
    serializer_class = ConversationSerializer


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
    queryset = Message.objects.order_by("-client_timestamp")
    serializer_class = MessageSerializer


class MessageStatusViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = MessageStatus.objects.order_by("-updated_at")
    serializer_class = MessageStatusSerializer
