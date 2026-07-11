from datetime import timedelta

from api.models import default_part_request_expiry
from django.db import migrations, models


def backfill_part_request_expiries(apps, schema_editor):
    PartRequest = apps.get_model("api", "PartRequest")
    PartRequest.objects.filter(expires_at__isnull=True).update(
        expires_at=models.F("created_at") + timedelta(hours=48)
    )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0017_seed_expanded_car_catalog"),
    ]

    operations = [
        migrations.AddField(
            model_name="partrequest",
            name="expires_at",
            field=models.DateTimeField(db_index=True, null=True),
        ),
        migrations.RunPython(backfill_part_request_expiries, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="partrequest",
            name="expires_at",
            field=models.DateTimeField(
                db_index=True,
                default=default_part_request_expiry,
            ),
        ),
    ]
