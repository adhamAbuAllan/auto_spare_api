from django.contrib import admin

from .models import PartRequest, PartImage, PartRequestStatus


class PartImageInline(admin.TabularInline):
    model = PartImage
    extra = 1


@admin.register(PartRequest)
class PartRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "requester", "car_model", "status", "created_at")
    list_filter = ("status", "car_model__make", "created_at")
    search_fields = (
        "title",
        "description",
        "requester__username",
        "car_model__name",
        "car_model__make__name",
    )
    inlines = [PartImageInline]
    ordering = ("-created_at",)


@admin.register(PartRequestStatus)
class PartRequestStatusAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "label", "is_terminal")
    ordering = ("-created_at",)
