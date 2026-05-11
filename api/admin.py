from django.contrib import admin

from .models import (
    CarMake,
    CarModel,
    MobileDevice,
    PartRequestAccess,
    SparePart,
    UserCarModel,
    UserReport,
)


@admin.register(SparePart)
class SparePartAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "price", "created_at")
    search_fields = ("name",)
    ordering = ("-created_at",)


@admin.register(CarMake)
class CarMakeAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug", "created_at")
    search_fields = ("name", "slug")
    ordering = ("name",)


@admin.register(CarModel)
class CarModelAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "make", "is_active", "created_at")
    list_filter = ("make", "is_active")
    search_fields = ("name", "make__name", "slug")
    ordering = ("make__name", "name")


@admin.register(UserCarModel)
class UserCarModelAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "car_model", "created_at")
    list_filter = ("car_model__make",)
    search_fields = ("user__username", "user__email", "car_model__name", "car_model__make__name")
    ordering = ("-created_at",)


@admin.register(PartRequestAccess)
class PartRequestAccessAdmin(admin.ModelAdmin):
    list_display = ("id", "part_request", "user", "status", "conversation", "requested_at")
    list_filter = ("status",)
    search_fields = ("part_request__title", "user__username", "user__email")
    ordering = ("-requested_at",)


@admin.register(UserReport)
class UserReportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "reported_user",
        "reporter",
        "reason",
        "status",
        "reviewed_by",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "reported_user__username",
        "reported_user__email",
        "reporter__username",
        "reporter__email",
        "reason",
        "details",
        "admin_notes",
    )
    ordering = ("-created_at",)


@admin.register(MobileDevice)
class MobileDeviceAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "platform", "device_id", "is_active", "last_seen_at")
    list_filter = ("platform", "is_active")
    search_fields = ("user__username", "user__email", "device_id", "device_name")
    ordering = ("-last_seen_at", "-updated_at")
