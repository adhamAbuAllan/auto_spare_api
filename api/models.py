from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.text import slugify


class ApiUser(AbstractUser):
    ROLE_USER = "user"
    ROLE_SUPPLIER = "supplier"
    ROLE_CHOICES = [
        (ROLE_USER, "User"),    
        (ROLE_SUPPLIER, "Supplier"),
    ]

    email = models.EmailField(unique=True, blank=True, null=True)
    name = models.CharField(max_length=120)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True)
    city = models.CharField(max_length=120, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_USER)
    rating = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    chat_push_enabled = models.BooleanField(default=True)
    chat_message_preview_enabled = models.BooleanField(default=True)
    chat_last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

 
    def __str__(self):
        label = self.name or self.email or self.username
        return f"{label} ({self.role})"


class SparePart(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class CarMake(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class CarModel(models.Model):
    make = models.ForeignKey(
        CarMake,
        on_delete=models.CASCADE,
        related_name="models",
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=160)
    image_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["make__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["make", "slug"],
                name="unique_car_model_per_make_slug",
            )
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.make.name} {self.name}"


class UserCarModel(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="car_model_links",
    )
    car_model = models.ForeignKey(
        CarModel,
        on_delete=models.CASCADE,
        related_name="user_links",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "car_model"],
                name="unique_user_car_model_link",
            )
        ]
        indexes = [
            models.Index(fields=["user", "car_model"]),
            models.Index(fields=["car_model"]),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.car_model_id}"


class PartRequestStatus(models.Model):
    code = models.CharField(max_length=50, unique=True)
    label = models.CharField(max_length=120)
    is_terminal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Part request status"
        verbose_name_plural = "Part request statuses"

    def __str__(self):
        return self.label


class PartRequest(models.Model):
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="part_requests"
    )
    title = models.CharField(max_length=255)
    title_language = models.CharField(max_length=12, blank=True)
    description = models.TextField(blank=True)
    description_language = models.CharField(max_length=12, blank=True)
    min_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.ForeignKey(
        PartRequestStatus, on_delete=models.PROTECT, related_name="part_requests"
    )
    car_model = models.ForeignKey(
        CarModel,
        on_delete=models.PROTECT,
        related_name="part_requests",
        null=True,
        blank=True,
    )
    city = models.CharField(max_length=120, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class PartImage(models.Model):
    part_request = models.ForeignKey(
        PartRequest, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField(upload_to="part_requests/")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Image for {self.part_request_id}"


class TranslationCache(models.Model):
    entity_type = models.CharField(max_length=50)
    entity_id = models.PositiveBigIntegerField()
    field_name = models.CharField(max_length=50)
    target_language = models.CharField(max_length=12)
    source_language = models.CharField(max_length=12, blank=True)
    source_hash = models.CharField(max_length=64)
    translated_text = models.TextField()
    provider = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["entity_type", "entity_id", "field_name", "target_language"],
                name="unique_translation_cache_entry",
            )
        ]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["target_language"]),
        ]

    def __str__(self):
        return (
            f"{self.entity_type}:{self.entity_id}:{self.field_name}"
            f"->{self.target_language}"
        )


class Conversation(models.Model):
    title = models.CharField(max_length=255, blank=True)
    last_message = models.ForeignKey(
        "Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    last_message_time = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title or f"Conversation {self.id}"


class ConversationParticipant(models.Model):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="participants"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations"
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "user"],
                name="unique_participant_per_conversation",
            )
        ]
        indexes = [
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return f"{self.user_id} in {self.conversation_id}"


class PartRequestAccess(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_REJECTED = "rejected"
    STATUS_REVOKED = "revoked"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_REVOKED, "Revoked"),
    ]

    part_request = models.ForeignKey(
        PartRequest,
        on_delete=models.CASCADE,
        related_name="access_requests",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="part_request_accesses",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="part_request_accesses",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_part_request_accesses",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["part_request", "user"],
                name="unique_part_request_access_per_user",
            )
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["part_request", "status"]),
            models.Index(fields=["conversation", "status"]),
        ]

    def __str__(self):
        return f"access:{self.part_request_id}:{self.user_id}={self.status}"




class MobileDevice(models.Model):
    PLATFORM_ANDROID = "android"
    PLATFORM_IOS = "ios"
    PLATFORM_WEB = "web"
    PLATFORM_CHOICES = [
        (PLATFORM_ANDROID, "Android"),
        (PLATFORM_IOS, "iOS"),
        (PLATFORM_WEB, "Web"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mobile_devices"
    )
    device_id = models.CharField(max_length=191)
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    push_token = models.CharField(max_length=255, blank=True)
    device_name = models.CharField(max_length=120, blank=True)
    app_version = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "device_id"],
                name="unique_mobile_device_per_user",
            )
        ]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["platform"]),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.platform}:{self.device_id}"


class Message(models.Model):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="messages"
    )
    MESSAGE_TYPES = [
        ("text", "Text"),
        ("media", "Media"),
        ("product", "Product"),
    ]

    message_type = models.CharField(max_length=20, choices=MESSAGE_TYPES, default="text")
    text = models.TextField(blank=True)
    text_language = models.CharField(max_length=12, blank=True)
    product = models.ForeignKey(
        PartRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    reply_to = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies"
    )
    client_timestamp = models.DateTimeField()
    server_timestamp = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["conversation", "client_timestamp", "server_timestamp"]),
        ]

    def __str__(self):
        return f"Message {self.id} in {self.conversation_id}"


class MessageHiddenForUser(models.Model):
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="hidden_for_users"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hidden_messages",
    )
    hidden_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "user"],
                name="unique_hidden_message_per_user",
            )
        ]
        indexes = [
            models.Index(fields=["user", "hidden_at"]),
            models.Index(fields=["message", "user"]),
        ]

    def __str__(self):
        return f"hide:{self.message_id}:{self.user_id}"


class MessageAttachment(models.Model):
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to="message_attachments/")
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Attachment {self.id} for message {self.message_id}"


class MessageStatus(models.Model):
    STATUS_SENT = "sent"
    STATUS_DELIVERED = "delivered"
    STATUS_SEEN = "seen"
    STATUS_CHOICES = [
        (STATUS_SENT, "Sent"),
        (STATUS_DELIVERED, "Delivered"),
        (STATUS_SEEN, "Seen"),
    ]

    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="statuses"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="message_statuses"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SENT)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "user"], name="unique_message_status_per_user"
            )
        ]
        indexes = [
            models.Index(fields=["message", "user"]),
        ]

    def __str__(self):
        return f"{self.message_id}:{self.user_id}={self.status}"


class TypingStatus(models.Model):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="typing_statuses"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="typing_statuses"
    )
    is_typing = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user_id} typing in {self.conversation_id}"


class MessageReaction(models.Model):
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="reactions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="message_reactions"
    )
    emoji = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "user"],
                name="unique_reaction_per_user",
            )
        ]

    def __str__(self):
        return f"{self.emoji} by {self.user_id} on {self.message_id}"


class Plan(models.Model):
    INTERVAL_MONTH = "month"
    INTERVAL_YEAR = "year"
    INTERVAL_CHOICES = [
        (INTERVAL_MONTH, "Monthly"),
        (INTERVAL_YEAR, "Yearly"),
    ]

    name = models.CharField(max_length=120)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="USD")
    interval = models.CharField(max_length=10, choices=INTERVAL_CHOICES)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Subscription(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_CANCELED = "canceled"
    STATUS_PAST_DUE = "past_due"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_CANCELED, "Canceled"),
        (STATUS_PAST_DUE, "Past due"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    start_date = models.DateField()
    end_date = models.DateTimeField(null=True, blank=True)
    auto_renew = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user_id} - {self.plan_id} ({self.status})"


class Payment(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
    ]

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="USD")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    provider = models.CharField(max_length=50, blank=True)
    transaction_id = models.CharField(max_length=120, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.subscription_id} {self.amount} {self.currency}"
