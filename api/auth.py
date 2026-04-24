from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView


class ClearTokenObtainPairSerializer(TokenObtainPairSerializer):
    default_error_messages = {
        **TokenObtainPairSerializer.default_error_messages,
        "user_not_found": "No user found with this username.",
        "invalid_password": "The password you entered is incorrect.",
        "inactive_account": "This account is inactive. Please contact support.",
    }

    def validate(self, attrs):
        username = str(attrs.get(self.username_field, "")).strip()
        password = attrs.get("password", "")
        user_model = get_user_model()
        lookup = {user_model.USERNAME_FIELD: username}
        user = user_model._default_manager.filter(**lookup).first()

        if user is None:
            raise AuthenticationFailed(
                self.error_messages["user_not_found"],
                code="user_not_found",
            )

        if not user.check_password(password):
            raise AuthenticationFailed(
                self.error_messages["invalid_password"],
                code="invalid_password",
            )

        if not user.is_active:
            raise AuthenticationFailed(
                self.error_messages["inactive_account"],
                code="inactive_account",
            )

        return super().validate(attrs)


class ClearTokenObtainPairView(TokenObtainPairView):
    serializer_class = ClearTokenObtainPairSerializer
