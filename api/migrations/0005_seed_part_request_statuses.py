from django.db import migrations


DEFAULT_PART_REQUEST_STATUSES = [
    {"code": "awaiting", "label": "Awaiting", "is_terminal": False},
    {"code": "cancelled", "label": "Cancelled", "is_terminal": True},
]


def seed_part_request_statuses(apps, schema_editor):
    PartRequestStatus = apps.get_model("api", "PartRequestStatus")

    for status in DEFAULT_PART_REQUEST_STATUSES:
        PartRequestStatus.objects.update_or_create(
            code=status["code"],
            defaults={
                "label": status["label"],
                "is_terminal": status["is_terminal"],
            },
        )


def remove_seeded_part_request_statuses(apps, schema_editor):
    PartRequestStatus = apps.get_model("api", "PartRequestStatus")
    PartRequestStatus.objects.filter(
        code__in=[status["code"] for status in DEFAULT_PART_REQUEST_STATUSES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0004_apiuser_chat_message_preview_enabled_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_part_request_statuses, remove_seeded_part_request_statuses),
    ]
