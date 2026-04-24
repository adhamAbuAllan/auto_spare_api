from django.db import IntegrityError, transaction
from rest_framework import serializers

from chat.runtime import get_globally_connected_user_ids

from .models import (
    ApiUser,
    CarMake,
    CarModel,
    Conversation,
    ConversationParticipant,
    Message,
    MessageAttachment,
    MessageReaction,
    MessageStatus,
    MobileDevice,
    PartImage,
    PartRequest,
    PartRequestAccess,
    PartRequestStatus,
    Payment,
    Plan,
    SparePart,
    Subscription,
    TypingStatus,
    UserCarModel,
)
from .translation import stamp_part_request_languages


class ApiUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)
    rating = serializers.DecimalField(
        max_digits=3, decimal_places=2, required=False, allow_null=True
    )
    email = serializers.EmailField(required=True)
    username = serializers.CharField(required=False, allow_blank=True)
    supported_car_model_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        write_only=True,
    )

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
            "supported_car_model_ids",
            "created_at",
            "password",
        ]
        read_only_fields = ["id", "created_at"]

    def validate_supported_car_model_ids(self, value):
        model_ids = [int(item) for item in value]
        unique_model_ids = list(dict.fromkeys(model_ids))
        available_model_ids = set(
            CarModel.objects.filter(id__in=unique_model_ids, is_active=True).values_list(
                "id", flat=True
            )
        )
        missing_model_ids = [
            model_id for model_id in unique_model_ids if model_id not in available_model_ids
        ]
        if missing_model_ids:
            raise serializers.ValidationError(
                "One or more selected car models do not exist or are inactive."
            )
        return unique_model_ids

    def validate(self, attrs):
        attrs = super().validate(attrs)

        email = str(attrs.get("email", "")).strip()
        username = str(attrs.get("username", "")).strip() or email
        role = str(
            attrs.get(
                "role",
                getattr(self.instance, "role", ApiUser.ROLE_USER),
            )
            or ApiUser.ROLE_USER
        ).strip()

        if email:
            attrs["email"] = email
        attrs["username"] = username
        if role != ApiUser.ROLE_SUPPLIER:
            attrs["supported_car_model_ids"] = []

        queryset = ApiUser.objects.all()
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)

        errors = {}
        if email and queryset.filter(email__iexact=email).exists():
            errors["email"] = "A user with this email already exists."
        if username and queryset.filter(username__iexact=username).exists():
            errors["username"] = "A user with this username already exists."

        if errors:
            raise serializers.ValidationError(errors)

        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        supported_car_model_ids = validated_data.pop("supported_car_model_ids", [])
        if not validated_data.get("username"):
            validated_data["username"] = validated_data.get("email")
        try:
            with transaction.atomic():
                user = ApiUser(**validated_data)
                if password:
                    user.set_password(password)
                else:
                    user.set_unusable_password()
                user.save()

                if supported_car_model_ids:
                    UserCarModel.objects.bulk_create(
                        [
                            UserCarModel(user=user, car_model_id=model_id)
                            for model_id in supported_car_model_ids
                        ]
                    )
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {"detail": "A user with this email or username already exists."}
            ) from exc
        return user


class SparePartSerializer(serializers.ModelSerializer):
    class Meta:
        model = SparePart
        fields = ["id", "name", "description", "price", "created_at"]
        read_only_fields = ["id", "created_at"]


class CarModelSerializer(serializers.ModelSerializer):
    make_id = serializers.IntegerField(source="make.id", read_only=True)
    make_name = serializers.CharField(source="make.name", read_only=True)
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = CarModel
        fields = [
            "id",
            "make_id",
            "make_name",
            "name",
            "display_name",
            "image_url",
            "is_active",
        ]
        read_only_fields = fields

    def get_display_name(self, obj):
        return f"{obj.make.name} {obj.name}"


class CarMakeSerializer(serializers.ModelSerializer):
    models = serializers.SerializerMethodField()

    class Meta:
        model = CarMake
        fields = ["id", "name", "slug", "models"]
        read_only_fields = fields

    def get_models(self, obj):
        return CarModelSerializer(
            obj.models.filter(is_active=True),
            many=True,
            context=self.context,
        ).data


class RequestAccessUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiUser
        fields = ["id", "name", "avatar"]
        read_only_fields = fields


class PartRequestStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartRequestStatus
        fields = ["id", "code", "label", "is_terminal", "created_at"]
        read_only_fields = ["id", "created_at"]


class PartRequestSerializer(serializers.ModelSerializer):
    car_model = serializers.PrimaryKeyRelatedField(
        queryset=CarModel.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    images = serializers.SerializerMethodField()
    requester_details = RequestAccessUserSerializer(source="requester", read_only=True)
    car_model_details = CarModelSerializer(source="car_model", read_only=True)
    status_details = PartRequestStatusSerializer(source="status", read_only=True)
    is_owner = serializers.SerializerMethodField()
    can_update_status = serializers.SerializerMethodField()
    my_access_status = serializers.SerializerMethodField()
    granted_user = serializers.SerializerMethodField()

    class Meta:
        model = PartRequest
        fields = [
            "id",
            "requester",
            "requester_details",
            "title",
            "title_language",
            "description",
            "description_language",
            "min_price",
            "max_price",
            "status",
            "status_details",
            "car_model",
            "car_model_details",
            "city",
            "images",
            "is_owner",
            "can_update_status",
            "my_access_status",
            "granted_user",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "requester",
            "title_language",
            "description_language",
            "created_at",
            "updated_at",
        ]

    def _request_user(self):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or user.is_anonymous:
            return None
        return user

    def _access_entries(self, obj):
        prefetched = getattr(obj, "_prefetched_objects_cache", {})
        entries = prefetched.get("access_requests")
        if entries is not None:
            return list(entries)
        return list(
            obj.access_requests.select_related("user", "resolved_by").all()
        )

    def _accepted_access(self, obj):
        for entry in self._access_entries(obj):
            if entry.status == PartRequestAccess.STATUS_ACCEPTED:
                return entry
        return None

    def _access_for_user(self, obj, user):
        if user is None:
            return None
        for entry in self._access_entries(obj):
            if entry.user_id == user.id:
                return entry
        return None

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if "city" in attrs:
            city = attrs.get("city")
            if city is None:
                attrs["city"] = None
            else:
                normalized = str(city).strip()
                attrs["city"] = normalized or None
        return attrs

    def get_images(self, obj):
        return PartImageSerializer(
            obj.images.all(),
            many=True,
            context=self.context,
        ).data

    def get_is_owner(self, obj):
        user = self._request_user()
        return bool(user and obj.requester_id == user.id)

    def get_can_update_status(self, obj):
        user = self._request_user()
        if user is None:
            return False
        if obj.requester_id == user.id:
            return True
        access = self._access_for_user(obj, user)
        return bool(access and access.status == PartRequestAccess.STATUS_ACCEPTED)

    def get_my_access_status(self, obj):
        user = self._request_user()
        if user is None or obj.requester_id == user.id:
            return None
        access = self._access_for_user(obj, user)
        return access.status if access else None

    def get_granted_user(self, obj):
        access = self._accepted_access(obj)
        if access is None:
            return None
        return RequestAccessUserSerializer(
            access.user,
            context=self.context,
        ).data

    def create(self, validated_data):
        part_request = super().create(validated_data)
        stamp_part_request_languages(part_request)
        part_request.save(update_fields=["title_language", "description_language"])
        return part_request

    def update(self, instance, validated_data):
        part_request = super().update(instance, validated_data)
        stamp_part_request_languages(part_request)
        part_request.save(
            update_fields=[
                "title",
                "title_language",
                "description",
                "description_language",
                "min_price",
                "max_price",
                "status",
                "car_model",
                "city",
                "updated_at",
            ]
        )
        return part_request


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
    push_token = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    device_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    app_version = serializers.CharField(required=False, allow_blank=True, allow_null=True)

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

    def _normalize_optional_text(self, value):
        if value is None:
            return ""
        return str(value).strip()

    def validate(self, attrs):
        is_active = attrs.get("is_active", getattr(self.instance, "is_active", True))
        push_token = self._normalize_optional_text(
            attrs.get("push_token", getattr(self.instance, "push_token", ""))
        )
        device_id = self._normalize_optional_text(
            attrs.get("device_id", getattr(self.instance, "device_id", ""))
        )

        if not device_id:
            raise serializers.ValidationError({"device_id": "device_id is required."})
        if is_active and not push_token:
            raise serializers.ValidationError(
                {"push_token": "push_token is required for active mobile devices."}
            )

        attrs["device_id"] = device_id
        if "push_token" in attrs:
            attrs["push_token"] = push_token
        if "device_name" in attrs:
            attrs["device_name"] = self._normalize_optional_text(attrs.get("device_name"))
        if "app_version" in attrs:
            attrs["app_version"] = self._normalize_optional_text(attrs.get("app_version"))
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
            "text_language",
            "product",
            "reply_to",
            "client_timestamp",
            "server_timestamp",
            "edited_at",
            "is_deleted",
        ]
        read_only_fields = [
            "id",
            "text_language",
            "server_timestamp",
            "edited_at",
            "is_deleted",
        ]


class UserBriefSerializer(serializers.ModelSerializer):
    is_online = serializers.SerializerMethodField()
    last_seen_at = serializers.DateTimeField(source="chat_last_seen_at", read_only=True)

    class Meta:
        model = ApiUser
        fields = ["id", "name", "avatar", "is_online", "last_seen_at"]

    def get_is_online(self, obj):
        online_user_ids = self.context.get("_online_user_ids")
        if online_user_ids is None:
            online_user_ids = get_globally_connected_user_ids() or set()
            self.context["_online_user_ids"] = online_user_ids
        return obj.id in online_user_ids


def _serialize_supported_car_models(user, *, context):
    if user.role != ApiUser.ROLE_SUPPLIER:
        return []
    links = (
        user.car_model_links.select_related("car_model__make")
        .filter(car_model__is_active=True)
        .order_by("car_model__make__name", "car_model__name")
    )
    return CarModelSerializer(
        [link.car_model for link in links],
        many=True,
        context=context,
    ).data


class PublicUserProfileSerializer(UserBriefSerializer):
    supported_car_models = serializers.SerializerMethodField()

    class Meta(UserBriefSerializer.Meta):
        model = ApiUser
        fields = UserBriefSerializer.Meta.fields + [
            "email",
            "phone",
            "city",
            "role",
            "rating",
            "supported_car_models",
            "created_at",
        ]
        read_only_fields = fields

    def get_supported_car_models(self, obj):
        return _serialize_supported_car_models(obj, context=self.context)


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

    def _latest_messages_by_id(self):
        cached = self.context.get("_latest_messages_by_id")
        if cached is not None:
            return cached

        instances = []
        if isinstance(self.parent, serializers.ListSerializer):
            parent_instance = getattr(self.parent, "instance", None)
            if parent_instance is not None:
                instances = list(parent_instance)
        elif getattr(self, "instance", None) is not None:
            instances = [self.instance]

        message_ids = [
            getattr(item, "latest_message_id", None)
            for item in instances
            if getattr(item, "latest_message_id", None)
        ]
        if not message_ids:
            self.context["_latest_messages_by_id"] = {}
            return {}

        messages = (
            Message.objects.filter(id__in=message_ids)
            .select_related(
                "sender",
                "product",
                "product__status",
                "product__car_model__make",
            )
            .prefetch_related("statuses__message", "statuses")
        )
        messages_by_id = {message.id: message for message in messages}
        self.context["_latest_messages_by_id"] = messages_by_id
        return messages_by_id

    def get_last_message(self, obj):
        if not getattr(obj, "latest_message_id", None):
            return None
        latest_messages_by_id = self._latest_messages_by_id()
        latest_message = latest_messages_by_id.get(obj.latest_message_id)
        if latest_message is None:
            return None
        return {
            "id": latest_message.id,
            "message_type": latest_message.message_type,
            "text": latest_message.text,
            "text_language": latest_message.text_language or None,
            "sender": {
                "id": latest_message.sender_id,
                "name": latest_message.sender.name,
            },
            "timestamp": obj.latest_message_timestamp,
            "edited_at": latest_message.edited_at,
            "is_deleted": bool(latest_message.is_deleted),
            "product": (
                PartRequestBriefSerializer(
                    latest_message.product,
                    context=self.context,
                ).data
                if latest_message.product_id
                else None
            ),
            "statuses": [
                {
                    "conversation_id": int(status.message.conversation_id),
                    "message_id": int(status.message_id),
                    "user_id": int(status.user_id),
                    "status": status.status,
                    "updated_at": status.updated_at.isoformat(),
                }
                for status in latest_message.statuses.select_related("message").order_by("user_id")
            ],
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
    text = serializers.SerializerMethodField()
    text_language = serializers.CharField(read_only=True)
    product = serializers.SerializerMethodField()
    is_deleted = serializers.SerializerMethodField()
    edited_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "sender",
            "text",
            "text_language",
            "product",
            "client_timestamp",
            "server_timestamp",
            "edited_at",
            "is_deleted",
        ]

    def _is_hidden_for_request_user(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or user.is_anonymous:
            return False
        return obj.hidden_for_users.filter(user=user).exists()

    def get_is_deleted(self, obj):
        return obj.is_deleted or self._is_hidden_for_request_user(obj)

    def get_text(self, obj):
        if self.get_is_deleted(obj):
            return ""
        return obj.text

    def get_product(self, obj):
        if self.get_is_deleted(obj):
            return None
        if not obj.product:
            return None
        return {
            "id": obj.product.id,
            "title": obj.product.title,
            "title_language": obj.product.title_language or None,
            "min_price": obj.product.min_price,
            "max_price": obj.product.max_price,
            "car_model_details": (
                CarModelSerializer(obj.product.car_model, context=self.context).data
                if obj.product.car_model_id
                else None
            ),
        }


class PartRequestBriefSerializer(serializers.ModelSerializer):
    car_model_details = CarModelSerializer(source="car_model", read_only=True)
    status_details = PartRequestStatusSerializer(source="status", read_only=True)

    class Meta:
        model = PartRequest
        fields = [
            "id",
            "title",
            "title_language",
            "min_price",
            "max_price",
            "status",
            "status_details",
            "car_model_details",
        ]


class MessageStatusReadSerializer(serializers.ModelSerializer):
    conversation_id = serializers.IntegerField(source="message.conversation_id", read_only=True)
    message_id = serializers.IntegerField(read_only=True)
    user_id = serializers.IntegerField(read_only=True)

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
            "text_language",
            "media",
            "product",
            "reply_to",
            "client_timestamp",
            "server_timestamp",
            "edited_at",
            "is_deleted",
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


class MeSerializer(ApiUserSerializer):
    supported_car_models = serializers.SerializerMethodField()
    supported_car_model_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        write_only=True,
    )

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
            "supported_car_models",
            "supported_car_model_ids",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "email",
            "username",
            "role",
            "rating",
            "created_at",
        ]

    def validate_supported_car_model_ids(self, value):
        model_ids = [int(item) for item in value]
        unique_model_ids = list(dict.fromkeys(model_ids))
        available_model_ids = set(
            CarModel.objects.filter(id__in=unique_model_ids, is_active=True).values_list(
                "id", flat=True
            )
        )
        missing_model_ids = [
            model_id for model_id in unique_model_ids if model_id not in available_model_ids
        ]
        if missing_model_ids:
            raise serializers.ValidationError(
                "One or more selected car models do not exist or are inactive."
            )
        return unique_model_ids

    def get_supported_car_models(self, obj):
        return _serialize_supported_car_models(obj, context=self.context)

    def update(self, instance, validated_data):
        supported_car_model_ids = validated_data.pop("supported_car_model_ids", None)
        previous_avatar_name = str(getattr(instance.avatar, "name", "") or "").strip()
        previous_avatar_storage = getattr(instance.avatar, "storage", None)
        instance = super().update(instance, validated_data)

        if supported_car_model_ids is not None and instance.role == ApiUser.ROLE_SUPPLIER:
            existing_model_ids = set(
                UserCarModel.objects.filter(user=instance).values_list("car_model_id", flat=True)
            )
            next_model_ids = set(supported_car_model_ids)

            if existing_model_ids - next_model_ids:
                UserCarModel.objects.filter(
                    user=instance,
                    car_model_id__in=existing_model_ids - next_model_ids,
                ).delete()

            for model_id in next_model_ids - existing_model_ids:
                UserCarModel.objects.create(user=instance, car_model_id=model_id)

        current_avatar_name = str(getattr(instance.avatar, "name", "") or "").strip()
        if (
            previous_avatar_name
            and previous_avatar_name != current_avatar_name
            and previous_avatar_storage is not None
        ):
            try:
                previous_avatar_storage.delete(previous_avatar_name)
            except Exception:
                pass

        return instance


class MessageStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageStatus
        fields = ["id", "message", "user", "status", "updated_at"]
        read_only_fields = ["id", "user", "updated_at"]


class PartRequestAccessSerializer(serializers.ModelSerializer):
    part_request_details = PartRequestBriefSerializer(source="part_request", read_only=True)
    user_details = RequestAccessUserSerializer(source="user", read_only=True)
    resolved_by_details = RequestAccessUserSerializer(source="resolved_by", read_only=True)
    can_approve = serializers.SerializerMethodField()

    class Meta:
        model = PartRequestAccess
        fields = [
            "id",
            "part_request",
            "part_request_details",
            "conversation",
            "user",
            "user_details",
            "status",
            "resolved_by",
            "resolved_by_details",
            "requested_at",
            "resolved_at",
            "updated_at",
            "can_approve",
        ]
        read_only_fields = [
            "id",
            "user",
            "status",
            "resolved_by",
            "requested_at",
            "resolved_at",
            "updated_at",
            "can_approve",
        ]

    def get_can_approve(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or user.is_anonymous:
            return False
        return (
            obj.part_request.requester_id == user.id
            and obj.status == PartRequestAccess.STATUS_PENDING
        )


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
