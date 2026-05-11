from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = (
        "id",
        "username",
        "email",
        "name",
        "role",
        "is_staff",
        "is_active",
        "blocked_at",
        "created_at",
    )
    search_fields = ("username", "email", "name", "phone", "city")
    list_filter = ("role", "is_staff", "is_superuser", "is_active")
    ordering = ("-created_at",)
    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            "Marketplace",
            {
                "fields": (
                    "name",
                    "avatar",
                    "phone",
                    "city",
                    "role",
                    "rating",
                    "chat_push_enabled",
                    "chat_message_preview_enabled",
                    "chat_last_seen_at",
                    "blocked_at",
                    "blocked_reason",
                    "blocked_by",
                    "created_at",
                )
            },
        ),
    )
    readonly_fields = ("created_at", "chat_last_seen_at", "blocked_at", "blocked_by")
