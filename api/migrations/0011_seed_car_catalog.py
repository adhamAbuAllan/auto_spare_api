from urllib.parse import quote_plus

from django.db import migrations
from django.utils.text import slugify


CAR_CATALOG = {
    "Toyota": ["Camry", "Corolla", "Hilux", "Land Cruiser", "Prado", "Yaris"],
    "Hyundai": ["Elantra", "Sonata", "Tucson", "Accent", "Santa Fe"],
    "Kia": ["Cerato", "Sportage", "Rio", "Sorento", "Picanto"],
    "Nissan": ["Sunny", "Sentra", "Altima", "Patrol", "X-Trail"],
    "Honda": ["Civic", "Accord", "CR-V", "City", "Pilot"],
    "Ford": ["Focus", "Fusion", "Explorer", "Ranger", "Territory"],
    "Chevrolet": ["Cruze", "Malibu", "Captiva", "Tahoe", "Silverado"],
    "Mercedes-Benz": ["A-Class", "C-Class", "E-Class", "GLC", "GLE"],
    "BMW": ["1 Series", "3 Series", "5 Series", "X3", "X5"],
    "Audi": ["A3", "A4", "A6", "Q3", "Q5"],
    "Volkswagen": ["Golf", "Passat", "Tiguan", "Jetta", "Touareg"],
    "Mazda": ["Mazda 3", "Mazda 6", "CX-5", "CX-9", "BT-50"],
    "Mitsubishi": ["Lancer", "Attrage", "Pajero", "Outlander", "L200"],
    "Renault": ["Logan", "Duster", "Megane", "Sandero", "Koleos"],
    "Peugeot": ["301", "2008", "3008", "508", "Partner"],
    "Fiat": ["Tipo", "500", "Doblo", "Panda", "Egea"],
    "Skoda": ["Fabia", "Octavia", "Superb", "Karoq", "Kodiaq"],
    "MG": ["MG 5", "MG 6", "ZS", "HS", "RX5"],
    "Chery": ["Arrizo 5", "Arrizo 6", "Tiggo 2", "Tiggo 7", "Tiggo 8"],
    "Geely": ["Emgrand", "Coolray", "Azkarra", "Tugella", "Okavango"],
    "BYD": ["F3", "Qin Plus", "Song Plus", "Atto 3", "Seal"],
}


def _placeholder_image_url(make_name, model_name):
    label = quote_plus(f"{make_name} {model_name}")
    return f"https://placehold.co/600x400/png?text={label}"


def seed_car_catalog(apps, schema_editor):
    CarMake = apps.get_model("api", "CarMake")
    CarModel = apps.get_model("api", "CarModel")

    for make_name, model_names in CAR_CATALOG.items():
        make, _ = CarMake.objects.update_or_create(
            slug=slugify(make_name),
            defaults={"name": make_name},
        )
        for model_name in model_names:
            CarModel.objects.update_or_create(
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
        ("api", "0010_car_catalog_and_request_car_model"),
    ]

    operations = [
        migrations.RunPython(seed_car_catalog, migrations.RunPython.noop),
    ]
