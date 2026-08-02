from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView


def _normalize_login_phone(value):
    return str(value or "").strip().replace(" ", "").replace("-", "")


def _login_phone_query(username_field, phone):
    query = Q(**{username_field: phone})
    if phone.startswith("+"):
        query |= Q(**{username_field: phone[1:]})
    return query


class ClearTokenObtainPairSerializer(TokenObtainPairSerializer):
    default_error_messages = {
        **TokenObtainPairSerializer.default_error_messages,
        "user_not_found": "No user found with this phone number.",
        "invalid_password": "The password you entered is incorrect.",
        "inactive_account": "This account is inactive. Please contact support.",
        "blocked_account": "This account has been blocked by an administrator.",
    }

    def validate(self, attrs):
        phone = _normalize_login_phone(attrs.get(self.username_field) or attrs.get("phone"))
        password = attrs.get("password", "")
        attrs[self.username_field] = phone
        user_model = get_user_model()
        user = (
            user_model._default_manager
            .filter(_login_phone_query(user_model.USERNAME_FIELD, phone))
            .first()
        )
        if user is not None:
            attrs[self.username_field] = getattr(user, user_model.USERNAME_FIELD)

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

        if not user.is_active and getattr(user, "blocked_at", None):
            raise AuthenticationFailed(
                self.error_messages["blocked_account"],
                code="blocked_account",
            )

        if not user.is_active:
            raise AuthenticationFailed(
                self.error_messages["inactive_account"],
                code="inactive_account",
            )

        return super().validate(attrs)


class ClearTokenObtainPairView(TokenObtainPairView):
    serializer_class = ClearTokenObtainPairSerializer
