from django.urls import include, path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework.routers import DefaultRouter

from .views import (
    ApiUserViewSet,
    ConversationParticipantViewSet,
    ConversationViewSet,
    MeView,
    MessageStatusViewSet,
    MessageViewSet,
    PartImageViewSet,
    PartRequestStatusViewSet,
    PartRequestViewSet,
    SparePartViewSet,
    health,
)

router = DefaultRouter()
router.register("users", ApiUserViewSet, basename="users")
router.register("spare-parts", SparePartViewSet, basename="spare-parts")
router.register("part-request-statuses", PartRequestStatusViewSet, basename="part-request-statuses")
router.register("part-requests", PartRequestViewSet, basename="part-requests")
router.register("part-images", PartImageViewSet, basename="part-images")
router.register("conversations", ConversationViewSet, basename="conversations")
router.register(
    "conversation-participants",
    ConversationParticipantViewSet,
    basename="conversation-participants",
)
router.register("messages", MessageViewSet, basename="messages")
router.register("message-statuses", MessageStatusViewSet, basename="message-statuses")


urlpatterns = [
    path("health/", health, name="health"),
    path("me/", MeView.as_view(), name="me"),
    path("token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("", include(router.urls)),
]
