from django.db import migrations, models


def rename_read_to_seen(apps, schema_editor):
    MessageStatus = apps.get_model("api", "MessageStatus")
    MessageStatus.objects.filter(status="read").update(status="seen")


def rename_seen_to_read(apps, schema_editor):
    MessageStatus = apps.get_model("api", "MessageStatus")
    MessageStatus.objects.filter(status="seen").update(status="read")


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0002_partrequest_price_range"),
    ]

    operations = [
        migrations.RunPython(rename_read_to_seen, rename_seen_to_read),
        migrations.AlterField(
            model_name="messagestatus",
            name="status",
            field=models.CharField(
                choices=[
                    ("sent", "Sent"),
                    ("delivered", "Delivered"),
                    ("seen", "Seen"),
                ],
                default="sent",
                max_length=20,
            ),
        ),
    ]
