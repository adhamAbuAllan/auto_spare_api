from django.contrib import admin

from .models import Plan, Subscription, Payment


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "price", "interval", "is_active")
    ordering = ("-created_at",)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "plan", "status", "start_date", "end_date")
    list_filter = ("status",)
    ordering = ("-created_at",)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "subscription", "amount", "currency", "status", "created_at")
    list_filter = ("status",)
    ordering = ("-created_at",)
