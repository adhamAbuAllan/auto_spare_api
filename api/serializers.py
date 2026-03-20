from rest_framework import serializers

from .models import (
    ApiUser,
    Conversation,
    ConversationParticipant,
    Message,
    MessageAttachment,
    MessageReaction,
    MessageStatus,
    PartImage,
    PartRequest,
    PartRequestStatus,
    Payment,
    Plan,
    SparePart,
    Subscription,
    TypingStatus,
)


class ApiUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)
    rating = serializers.DecimalField(
        max_digits=3, decimal_places=2, required=False, allow_null=True
    )
    email = serializers.EmailField(required=True)
    username = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = ApiUser
        fields = [
            "id",
            "email",
            "username",
            "name",
            "phone",
            "city",
            "role",
            "rating",
            "created_at",
            "password",
        ]
        read_only_fields = ["id", "created_at"]

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        if not validated_data.get("username"):
            validated_data["username"] = validated_data.get("email")
        user = ApiUser(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user


class SparePartSerializer(serializers.ModelSerializer):
    class Meta:
        model = SparePart
        fields = ["id", "name", "description", "price", "created_at"]
        read_only_fields = ["id", "created_at"]


class PartRequestStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartRequestStatus
        fields = ["id", "code", "label", "is_terminal", "created_at"]
        read_only_fields = ["id", "created_at"]


class PartRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartRequest
        fields = [
            "id",
            "requester",
            "title",
            "description",
            "status",
            "city",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PartImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartImage
        fields = ["id", "part_request", "image", "created_at"]
        read_only_fields = ["id", "created_at"]


class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ["id", "title", "last_message", "last_message_time", "created_at"]
        read_only_fields = ["id", "last_message", "last_message_time", "created_at"]


class ConversationParticipantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConversationParticipant
        fields = ["id", "conversation", "user", "joined_at", "last_read_at"]
        read_only_fields = ["id", "joined_at"]


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "id",
            "conversation",
            "sender",
            "message_type",
            "text",
            "product",
            "reply_to",
            "client_timestamp",
            "server_timestamp",
            "is_deleted",
        ]
        read_only_fields = ["id", "server_timestamp", "is_deleted"]


class MessageAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageAttachment
        fields = ["id", "message", "file", "content_type", "size", "created_at"]
        read_only_fields = ["id", "created_at"]


class MessageStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageStatus
        fields = ["id", "message", "user", "status", "updated_at"]
        read_only_fields = ["id", "updated_at"]


class TypingStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = TypingStatus
        fields = ["id", "conversation", "user", "is_typing", "updated_at"]
        read_only_fields = ["id", "updated_at"]


class MessageReactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageReaction
        fields = ["id", "message", "user", "emoji", "created_at"]
        read_only_fields = ["id", "created_at"]


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ["id", "name", "price", "currency", "interval", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = [
            "id",
            "user",
            "plan",
            "status",
            "start_date",
            "end_date",
            "auto_renew",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id",
            "subscription",
            "amount",
            "currency",
            "status",
            "provider",
            "transaction_id",
            "paid_at",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]
