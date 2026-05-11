from django.core.management.base import BaseCommand, CommandError

from api.car_catalog_sync import CarImagesApiError, sync_car_catalog


class Command(BaseCommand):
    help = (
        "Sync car makes/models from carimagesapi.com and optionally fill missing "
        "model images into the local catalog."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--with-images",
            action="store_true",
            help="Also fetch and store model images from the external catalog.",
        )
        parser.add_argument(
            "--refresh-images",
            action="store_true",
            help="Refresh images even when a local image URL is already stored.",
        )
        parser.add_argument(
            "--make",
            action="append",
            dest="make_slugs",
            help="Limit the sync to one or more external make slugs such as bmw or toyota.",
        )

    def handle(self, *args, **options):
        try:
            stats = sync_car_catalog(
                with_images=bool(options["with_images"]),
                refresh_images=bool(options["refresh_images"]),
                make_slugs=options.get("make_slugs") or None,
            )
        except CarImagesApiError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS("Car catalog sync completed."))
        self.stdout.write(
            "Makes created: {makes_created}, makes updated: {makes_updated}, "
            "models created: {models_created}, models updated: {models_updated}, "
            "images updated: {images_updated}".format(**stats)
        )
