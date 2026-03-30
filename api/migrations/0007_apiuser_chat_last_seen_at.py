from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0006_normalize_part_request_statuses"),
    ]

    operations = [
        migrations.AddField(
            model_name="apiuser",
            name="chat_last_seen_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
