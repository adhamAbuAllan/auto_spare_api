from urllib.parse import quote_plus

from django.db import migrations
from django.utils.text import slugify


CAR_CATALOG = {
    # Toyota
    "Toyota": [
        "Camry", "Corolla", "Hilux", "Land Cruiser", "Prado", "Yaris", 
        "RAV4", "Fortuner", "Avalon", "Highlander", "Supra", "Sienna", 
        "Sequoia", "Prius", "C-HR"
    ],
    # Hyundai
    "Hyundai": [
        "Elantra", "Sonata", "Tucson", "Accent", "Santa Fe", 
        "Creta", "Kona", "Palisade", "Veloster", "Azera", "Ioniq 5"
    ],
    # Kia
    "Kia": [
        "Cerato", "Sportage", "Rio", "Sorento", "Picanto", 
        "Optima", "K5", "Pegas", "Telluride", "Seltos", "Carnival", "Stinger"
    ],
    # Nissan
    "Nissan": [
        "Sunny", "Sentra", "Altima", "Patrol", "X-Trail", 
        "Pathfinder", "Maxima", "Kicks", "Murano", "Navara", "GT-R", "Z"
    ],
    # Honda
    "Honda": [
        "Civic", "Accord", "CR-V", "City", "Pilot", "HR-V", "Odyssey"
    ],
    # Ford
    "Ford": [
        "Focus", "Fusion", "Explorer", "Ranger", "Territory", 
        "Mustang", "F-150", "Edge", "Taurus", "Bronco", "Expedition"
    ],
    # Chevrolet
    "Chevrolet": [
        "Cruze", "Malibu", "Captiva", "Tahoe", "Silverado", 
        "Camaro", "Corvette", "Trailblazer", "Suburban", "Trax", "Groove"
    ],
    # Mercedes-Benz
    "Mercedes-Benz": [
        "A-Class", "C-Class", "E-Class", "GLC", "GLE", 
        "S-Class", "G-Class", "CLA", "CLS", "GLA", "GLS", "AMG GT"
    ],
    # BMW
    "BMW": [
        "1 Series", "3 Series", "5 Series", "X3", "X5", 
        "7 Series", "M3", "M5", "X1", "X4", "X6", "X7", "iX"
    ],
    # Audi
    "Audi": [
        "A3", "A4", "A6", "Q3", "Q5", "A5", "A7", "A8", "Q7", "Q8"
    ],
    # Volkswagen
    "Volkswagen": ["Golf", "Passat", "Tiguan", "Jetta", "Touareg", "Teramont", "T-Roc"],
    # Mazda
    "Mazda": ["Mazda 3", "Mazda 6", "CX-5", "CX-9", "BT-50", "CX-30", "MX-5 Miata"],
    # Mitsubishi
    "Mitsubishi": ["Lancer", "Attrage", "Pajero", "Outlander", "L200", "Eclipse Cross", "Montero Sport"],
    # Renault
    "Renault": ["Logan", "Duster", "Megane", "Sandero", "Koleos", "Captur", "Symbol"],
    # Peugeot
    "Peugeot": ["301", "2008", "3008", "508", "Partner", "5008", "208"],
    # Fiat
    "Fiat": ["Tipo", "500", "Doblo", "Panda", "Egea", "Fiorino"],
    # Skoda
    "Skoda": ["Fabia", "Octavia", "Superb", "Karoq", "Kodiaq", "Rapid", "Scala"],
    # MG
    "MG": ["MG 5", "MG 6", "ZS", "HS", "RX5", "MG 3", "GT"],
    # Chery
    "Chery": ["Arrizo 5", "Arrizo 6", "Tiggo 2", "Tiggo 7", "Tiggo 8", "Tiggo 4 Pro"],
    # Geely
    "Geely": ["Emgrand", "Coolray", "Azkarra", "Tugella", "Okavango", "Monjaro", "Geometry C"],
    # BYD
    "BYD": ["F3", "Qin Plus", "Song Plus", "Atto 3", "Seal", "Han", "Tang"],

    # 24 new brands
    "Lexus": ["IS", "ES", "LS", "NX", "RX", "GX", "LX", "UX", "LC", "RC"],
    "Infiniti": ["Q50", "Q60", "QX50", "QX60", "QX80", "QX55"],
    "Porsche": ["911", "Cayenne", "Macan", "Panamera", "Taycan", "Boxster", "Cayman"],
    "Land Rover": ["Range Rover", "Range Rover Sport", "Defender", "Discovery", "Evoque", "Velar", "Discovery Sport"],
    "Jaguar": ["F-Pace", "E-Pace", "XF", "F-Type", "I-Pace", "XE"],
    "Volvo": ["XC90", "XC60", "XC40", "S90", "S60", "V60", "C40"],
    "Jeep": ["Grand Cherokee", "Wrangler", "Cherokee", "Compass", "Renegade", "Gladiator", "Commander"],
    "Dodge": ["Charger", "Challenger", "Durango", "Hornet", "Neon"],
    "Cadillac": ["Escalade", "XT5", "XT6", "CT4", "CT5", "XT4", "XT5 Sport"],
    "GMC": ["Sierra", "Yukon", "Acadia", "Terrain", "Canyon", "Savana"],
    "Lincoln": ["Navigator", "Aviator", "Nautilus", "Corsair"],
    "Subaru": ["Impreza", "Outback", "Forester", "Crosstrek", "Legacy", "WRX", "BRZ"],
    "Suzuki": ["Swift", "Jimny", "Vitara", "Baleno", "Ertiga", "Ciaz", "Dzire"],
    "Tesla": ["Model 3", "Model Y", "Model S", "Model X", "Cybertruck", "Roadster"],
    "Isuzu": ["D-Max", "MU-X"],
    "Genesis": ["G70", "G80", "G90", "GV70", "GV80"],
    "Cupra": ["Formentor", "Leon", "Ateca", "Born"],
    "Seat": ["Ibiza", "Leon", "Ateca", "Tarraco", "Arona"],
    "Alfa Romeo": ["Giulia", "Stelvio", "Tonale"],
    "Maserati": ["Ghibli", "Quattroporte", "Levante", "Grecale", "MC20"],
    "Changan": ["Alsvin", "Eado", "CS35 Plus", "CS75 Plus", "CS95", "UNI-T", "UNI-K", "UNI-V"],
    "GAC": ["GS3", "GS4", "GS8", "GA6", "GA8", "M8", "Empow"],
    "Haval": ["H6", "Jolion", "Dargo", "H9", "H2", "H7"],
    "Chrysler": ["300", "Pacifica"],
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
