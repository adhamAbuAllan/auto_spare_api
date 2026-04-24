from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0008_alter_partrequest_city_nullable"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="edited_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="MessageHiddenForUser",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("hidden_at", models.DateTimeField(auto_now_add=True)),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hidden_for_users",
                        to="api.message",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hidden_messages",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "hidden_at"], name="api_message_user_id_2b37cc_idx"),
                    models.Index(fields=["message", "user"], name="api_message_message_814346_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("message", "user"),
                        name="unique_hidden_message_per_user",
                    )
                ],
            },
        ),
    ]
