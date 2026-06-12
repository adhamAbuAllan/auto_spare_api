from django.urls import include, path, re_path
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework.routers import DefaultRouter

from chat.views import chat_tester
from .auth import ClearTokenObtainPairView
from .views import (
    ApiUserViewSet,
    car_catalog,
    CarMakeViewSet,
    CarModelViewSet,
    ConversationParticipantViewSet,
    ConversationViewSet,
    FirebaseRegistrationView,
    MeView,
    MobileDeviceViewSet,
    MessageStatusViewSet,
    MessageViewSet,
    PartImageViewSet,
    PartRequestAccessViewSet,
    PartRequestStatusViewSet,
    PartRequestViewSet,
    SparePartViewSet,
    UserReportViewSet,
    app_update,
    health,
)
from .car_images_proxy import car_images_api_proxy

router = DefaultRouter()
router.register("users", ApiUserViewSet, basename="users")
router.register("spare-parts", SparePartViewSet, basename="spare-parts")
router.register("car-makes", CarMakeViewSet, basename="car-makes")
router.register("car-models", CarModelViewSet, basename="car-models")
router.register("part-request-statuses", PartRequestStatusViewSet, basename="part-request-statuses")
router.register("part-requests", PartRequestViewSet, basename="part-requests")
router.register("part-request-accesses", PartRequestAccessViewSet, basename="part-request-accesses")
router.register("part-images", PartImageViewSet, basename="part-images")
router.register("conversations", ConversationViewSet, basename="conversations")
router.register(
    "conversation-participants",
    ConversationParticipantViewSet,
    basename="conversation-participants",
)
router.register("messages", MessageViewSet, basename="messages")
router.register("message-statuses", MessageStatusViewSet, basename="message-statuses")
router.register("mobile-devices", MobileDeviceViewSet, basename="mobile-devices")
router.register("user-reports", UserReportViewSet, basename="user-reports")


urlpatterns = [
    re_path(r"^v1/(?P<path>.*)$", car_images_api_proxy, name="car_images_api_proxy"),
    path("chat-tester/", chat_tester, name="chat_tester"),
    path("health/", health, name="health"),
    path("car-catalog/", car_catalog, name="car_catalog"),
    path("app-update/", app_update, name="app_update"),
    path("me/", MeView.as_view(), name="me"),
    path("register/", FirebaseRegistrationView.as_view(), name="register"),
    path("token/", ClearTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("", include(router.urls)),
]
