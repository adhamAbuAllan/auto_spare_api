from django.contrib import admin

from .models import CarMake, CarModel, SparePart, UserCarModel


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
