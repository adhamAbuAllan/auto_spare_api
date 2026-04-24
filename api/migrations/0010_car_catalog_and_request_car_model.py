from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0009_message_edited_at_messagehiddenforuser"),
    ]

    operations = [
        migrations.CreateModel(
            name="CarMake",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("slug", models.SlugField(max_length=140, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="CarModel",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("slug", models.SlugField(max_length=160)),
                ("image_url", models.URLField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "make",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="models",
                        to="api.carmake",
                    ),
                ),
            ],
            options={
                "ordering": ["make__name", "name"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("make", "slug"),
                        name="unique_car_model_per_make_slug",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="UserCarModel",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "car_model",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_links",
                        to="api.carmodel",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="car_model_links",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "car_model"], name="api_usercar_user_id_b484eb_idx"),
                    models.Index(fields=["car_model"], name="api_usercar_car_mod_5d4f50_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "car_model"),
                        name="unique_user_car_model_link",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="partrequest",
            name="car_model",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="part_requests",
                to="api.carmodel",
            ),
        ),
    ]
