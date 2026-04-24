from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0007_apiuser_chat_last_seen_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="partrequest",
            name="city",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
    ]
