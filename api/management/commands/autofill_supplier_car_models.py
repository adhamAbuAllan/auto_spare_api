from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from api.models import ApiUser, CarMake, CarModel, UserCarModel


class Command(BaseCommand):
    help = "Add all active models of the chosen car makes to the supplier's supported models."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be done without making changes to the database.",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            help="Limit the update to a specific user ID.",
        )
        parser.add_argument(
            "--make-slug",
            type=str,
            help="Limit the update to a specific car make slug (e.g. toyota).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        user_id = options.get("user_id")
        make_slug = options.get("make_slug")

        # Find suppliers (role = 'supplier')
        suppliers = ApiUser.objects.filter(role=ApiUser.ROLE_SUPPLIER)
        if user_id:
            suppliers = suppliers.filter(id=user_id)

        if not suppliers.exists():
            self.stdout.write("No matching suppliers found.")
            return

        self.stdout.write(f"Processing {suppliers.count()} suppliers...")

        total_added = 0

        for supplier in suppliers:
            # Get model IDs that this supplier currently supports
            supported_model_ids = UserCarModel.objects.filter(user=supplier).values_list("car_model_id", flat=True)
            if not supported_model_ids:
                continue

            # Find the distinct makes associated with these supported models
            makes_query = CarMake.objects.filter(models__id__in=supported_model_ids).distinct()
            if make_slug:
                makes_query = makes_query.filter(slug=make_slug)

            for make in makes_query:
                # Find all active models for this make
                all_make_models = CarModel.objects.filter(make=make, is_active=True)
                all_make_model_ids = set(all_make_models.values_list("id", flat=True))

                # Identify which models are missing for this supplier
                missing_model_ids = all_make_model_ids - set(supported_model_ids)
                if missing_model_ids:
                    self.stdout.write(
                        f"Supplier: {supplier.name} (Phone: {supplier.phone}, ID: {supplier.id}) "
                        f"is missing {len(missing_model_ids)} active models for make '{make.name}'."
                    )
                    
                    if not dry_run:
                        with transaction.atomic():
                            links_to_create = [
                                UserCarModel(user=supplier, car_model_id=model_id)
                                for model_id in missing_model_ids
                            ]
                            UserCarModel.objects.bulk_create(links_to_create)
                        self.stdout.write(self.style.SUCCESS(f"  Added {len(missing_model_ids)} models."))
                    else:
                        self.stdout.write(f"  [DRY RUN] Would add {len(missing_model_ids)} models.")
                    
                    total_added += len(missing_model_ids)

        if dry_run:
            self.stdout.write(self.style.WARNING(f"Dry run complete. Would have added {total_added} car model associations in total."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Finished. Added {total_added} car model associations in total."))
