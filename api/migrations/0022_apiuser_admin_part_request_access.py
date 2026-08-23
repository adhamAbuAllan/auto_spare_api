from django.db import migrations, models


LIMITED_ADMIN_PHONE_NUMBERS = ("+972566707515", "972566707515")


def restrict_limited_admin_part_request_access(apps, schema_editor):
    ApiUser = apps.get_model("api", "ApiUser")
    ApiUser.objects.filter(phone__in=LIMITED_ADMIN_PHONE_NUMBERS).update(
        admin_can_view_all_part_requests=False,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0021_apiuser_admin_chat_access"),
    ]

    operations = [
        migrations.AddField(
            model_name="apiuser",
            name="admin_can_view_all_part_requests",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(
            restrict_limited_admin_part_request_access,
            migrations.RunPython.noop,
        ),
    ]
