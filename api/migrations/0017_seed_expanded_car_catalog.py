from urllib.parse import quote_plus
from django.db import migrations
from django.utils.text import slugify
from api.mock_catalog_data import MOCK_CAR_CATALOG


def _placeholder_image_url(make_name, model_name):
    label = quote_plus(f"{make_name} {model_name}")
    return f"https://placehold.co/600x400/png?text={label}"


def seed_expanded_car_catalog(apps, schema_editor):
    CarMake = apps.get_model("api", "CarMake")
    CarModel = apps.get_model("api", "CarModel")

    for make_name, model_names in MOCK_CAR_CATALOG.items():
        make, _ = CarMake.objects.get_or_create(
            slug=slugify(make_name),
            defaults={"name": make_name},
        )
        for model_name in model_names:
            # We use get_or_create to avoid duplicating any models that already exist.
            # If a model exists, it is left untouched. Otherwise, it is created.
            CarModel.objects.get_or_create(
                make=make,
                slug=slugify(model_name),
                defaults={
                    "name": model_name,
                    "image_url": _placeholder_image_url(make_name, model_name),
                    "is_active": True,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0016_alter_apiuser_options_alter_apiuser_managers_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_expanded_car_catalog, migrations.RunPython.noop),
    ]
