from django.db import migrations


TARGET_PART_REQUEST_STATUSES = [
    {"code": "awaiting", "label": "Awaiting", "is_terminal": False},
    {"code": "cancelled", "label": "Cancelled", "is_terminal": True},
]


def normalize_part_request_statuses(apps, schema_editor):
    PartRequestStatus = apps.get_model("api", "PartRequestStatus")

    existing_by_code = {
        status.code: status for status in PartRequestStatus.objects.all()
    }

    for target in TARGET_PART_REQUEST_STATUSES:
        PartRequestStatus.objects.update_or_create(
            code=target["code"],
            defaults={
                "label": target["label"],
                "is_terminal": target["is_terminal"],
            },
        )

    target_codes = {status["code"] for status in TARGET_PART_REQUEST_STATUSES}
    for code, status in existing_by_code.items():
        if code not in target_codes:
            status.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0005_seed_part_request_statuses"),
    ]

    operations = [
        migrations.RunPython(normalize_part_request_statuses, migrations.RunPython.noop),
    ]
