from django.core.management.base import BaseCommand

from api.models import PartImage


class Command(BaseCommand):
    help = "Create missing thumbnails for existing part request images."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report missing thumbnails without creating them.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        images = PartImage.objects.filter(image__isnull=False).exclude(image="")
        images = images.filter(thumbnail__isnull=True) | images.filter(thumbnail="")

        created = 0
        for image in images.iterator():
            if dry_run:
                self.stdout.write(f"Would create thumbnail for image #{image.id}.")
                continue
            if image.generate_thumbnail():
                created += 1

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run complete."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Created {created} thumbnails."))
