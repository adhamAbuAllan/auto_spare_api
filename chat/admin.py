from django.contrib import admin

from .models import (
    Conversation,
    ConversationParticipant,
    Message,
    MessageAttachment,
    MessageStatus,
    TypingStatus,
    MessageReaction,
)


class ConversationParticipantInline(admin.TabularInline):
    model = ConversationParticipant
    extra = 1
    raw_id_fields = ("user",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at")
    inlines = [ConversationParticipantInline]
    ordering = ("-created_at",)


class MessageAttachmentInline(admin.TabularInline):
    model = MessageAttachment
    extra = 1


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "sender", "message_type", "client_timestamp")
    list_filter = ("message_type",)
    search_fields = ("text",)
    inlines = [MessageAttachmentInline]
    ordering = ("-client_timestamp",)
    raw_id_fields = ("conversation", "sender", "reply_to")


@admin.register(MessageStatus)
class MessageStatusAdmin(admin.ModelAdmin):
    list_display = ("message", "user", "status", "updated_at")
    list_filter = ("status",)
    ordering = ("-updated_at",)
    raw_id_fields = ("message", "user")


@admin.register(TypingStatus)
class TypingStatusAdmin(admin.ModelAdmin):
    list_display = ("conversation", "user", "is_typing", "updated_at")
    ordering = ("-updated_at",)


@admin.register(MessageReaction)
class MessageReactionAdmin(admin.ModelAdmin):
    list_display = ("message", "user", "emoji", "created_at")
    ordering = ("-created_at",)
