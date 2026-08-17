from django.db import migrations, models


LIMITED_ADMIN_PHONE_NUMBERS = ("+972566707515", "972566707515")


def grant_limited_admin_access(apps, schema_editor):
    ApiUser = apps.get_model("api", "ApiUser")
    ApiUser.objects.filter(phone__in=LIMITED_ADMIN_PHONE_NUMBERS).update(
        is_staff=True,
        admin_can_view_all_chats=False,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0020_partimage_thumbnail"),
    ]

    operations = [
        migrations.AddField(
            model_name="apiuser",
            name="admin_can_view_all_chats",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(grant_limited_admin_access, migrations.RunPython.noop),
    ]
