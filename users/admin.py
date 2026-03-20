from django.contrib import admin

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "name", "role", "city")
    search_fields = ("email", "name")
    list_filter = ("role",)
    ordering = ("-created_at",)
