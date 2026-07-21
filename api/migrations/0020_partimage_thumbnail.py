from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0019_mobiledevice_notification_language"),
    ]

    operations = [
        migrations.AddField(
            model_name="partimage",
            name="thumbnail",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="part_request_thumbnails/",
            ),
        ),
    ]
