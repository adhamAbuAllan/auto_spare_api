from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = (
        "id",
        "phone",
        "name",
        "role",
        "is_staff",
        "is_active",
        "blocked_at",
        "created_at",
    )
    search_fields = ("phone", "name", "city")
    list_filter = ("role", "is_staff", "is_superuser", "is_active")
    ordering = ("-created_at",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "phone",
                    "password",
                )
            },
        ),
        (
            "Profile",
            {
                "fields": (
                    "name",
                    "avatar",
                    "city",
                    "role",
                    "rating",
                    "firebase_uid",
                    "phone_verified_at",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Marketplace",
            {
                "fields": (
                    "chat_push_enabled",
                    "chat_message_preview_enabled",
                    "chat_last_seen_at",
                    "blocked_at",
                    "blocked_reason",
                    "blocked_by",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined", "created_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("phone", "name", "password1", "password2"),
            },
        ),
    )
    readonly_fields = (
        "created_at",
        "chat_last_seen_at",
        "blocked_at",
        "blocked_by",
        "last_login",
        "date_joined",
        "phone_verified_at",
    )
