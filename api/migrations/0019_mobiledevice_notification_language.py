from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0018_partrequest_expires_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="mobiledevice",
            name="notification_language",
            field=models.CharField(default="en", max_length=12),
        ),
    ]
