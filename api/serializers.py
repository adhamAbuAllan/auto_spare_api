from rest_framework import serializers

from .models import (
    ApiUser,
    Conversation,
    ConversationParticipant,
    Message,
    MessageAttachment,
    MessageReaction,
    MessageStatus,
    MobileDevice,
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
            "avatar",
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
            "min_price",
            "max_price",
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
        read_only_fields = ["id", "joined_at", "last_read_at"]


class MobileDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MobileDevice
        fields = [
            "id",
            "device_id",
            "platform",
            "push_token",
            "device_name",
            "app_version",
            "is_active",
            "last_seen_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "last_seen_at", "created_at", "updated_at"]

    def validate(self, attrs):
        is_active = attrs.get("is_active", getattr(self.instance, "is_active", True))
        push_token = attrs.get("push_token", getattr(self.instance, "push_token", "")).strip()
        device_id = attrs.get("device_id", getattr(self.instance, "device_id", "")).strip()

        if not device_id:
            raise serializers.ValidationError({"device_id": "device_id is required."})
        if is_active and not push_token:
            raise serializers.ValidationError(
                {"push_token": "push_token is required for active mobile devices."}
            )

        attrs["device_id"] = device_id
        if "push_token" in attrs:
            attrs["push_token"] = push_token
        return attrs


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


class UserBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiUser
        fields = ["id", "name", "avatar"]


class ConversationParticipantReadSerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)

    class Meta:
        model = ConversationParticipant
        fields = ["id", "user", "joined_at", "last_read_at"]


class ConversationListSerializer(serializers.ModelSerializer):
    participants = ConversationParticipantReadSerializer(many=True, read_only=True)
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Conversation
        fields = ["id", "title", "participants", "last_message", "unread_count"]

    def get_last_message(self, obj):
        if not getattr(obj, "latest_message_id", None):
            return None
        return {
            "id": obj.latest_message_id,
            "text": obj.latest_message_text,
            "sender": {
                "id": obj.latest_message_sender_id,
                "name": obj.latest_message_sender_name,
            },
            "timestamp": obj.latest_message_server_timestamp,
        }


class MessageAttachmentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = MessageAttachment
        fields = ["id", "file_url", "content_type", "size", "created_at"]
        read_only_fields = ["id", "created_at"]

    def get_file_url(self, obj):
        request = self.context.get("request")
        if not obj.file:
            return None
        url = obj.file.url
        return request.build_absolute_uri(url) if request else url


class MessageReplySerializer(serializers.ModelSerializer):
    sender = UserBriefSerializer(read_only=True)
    product = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "sender",
            "text",
            "product",
            "client_timestamp",
            "server_timestamp",
        ]

    def get_product(self, obj):
        if not obj.product:
            return None
        return {
            "id": obj.product.id,
            "title": obj.product.title,
            "min_price": obj.product.min_price,
            "max_price": obj.product.max_price,
        }


class PartRequestBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartRequest
        fields = ["id", "title", "min_price", "max_price"]


class MessageStatusReadSerializer(serializers.ModelSerializer):
    conversation_id = serializers.IntegerField(source="message.conversation_id", read_only=True)
    message_id = serializers.IntegerField(source="message_id", read_only=True)
    user_id = serializers.IntegerField(source="user_id", read_only=True)

    class Meta:
        model = MessageStatus
        fields = ["conversation_id", "message_id", "user_id", "status", "updated_at"]


class MessageListSerializer(serializers.ModelSerializer):
    conversation_id = serializers.IntegerField(read_only=True)
    sender = UserBriefSerializer(read_only=True)
    product = PartRequestBriefSerializer(read_only=True)
    reply_to = MessageReplySerializer(read_only=True)
    media = MessageAttachmentSerializer(source="attachments", many=True, read_only=True)
    statuses = MessageStatusReadSerializer(many=True, read_only=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "conversation_id",
            "sender",
            "message_type",
            "text",
            "media",
            "product",
            "reply_to",
            "client_timestamp",
            "server_timestamp",
            "statuses",
        ]


class MessageCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "id",
            "conversation",
            "message_type",
            "text",
            "product",
            "reply_to",
            "client_timestamp",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs):
        message_type = attrs.get("message_type", "text")
        text = attrs.get("text", "").strip()
        product = attrs.get("product")

        if message_type == "text" and not text:
            raise serializers.ValidationError({"text": "Text is required for text messages."})
        if message_type == "product" and not product:
            raise serializers.ValidationError({"product": "Product is required for product messages."})
        return attrs


class MeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiUser
        fields = [
            "id",
            "email",
            "username",
            "name",
            "avatar",
            "phone",
            "city",
            "role",
            "rating",
            "chat_push_enabled",
            "chat_message_preview_enabled",
            "created_at",
        ]
        read_only_fields = ["id", "email", "username", "role", "rating", "created_at"]


class MessageStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageStatus
        fields = ["id", "message", "user", "status", "updated_at"]
        read_only_fields = ["id", "user", "updated_at"]


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
