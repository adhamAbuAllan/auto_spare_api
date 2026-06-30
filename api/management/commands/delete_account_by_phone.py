from django.core.management.base import BaseCommand, CommandError
from rest_framework import serializers

from api.account_deletion import delete_account
from api.models import ApiUser
from api.serializers import normalize_phone_number


class Command(BaseCommand):
    help = "Delete an account by phone number."

    def add_arguments(self, parser):
        parser.add_argument("phone", help="Account phone number in E.164 format, for example +966555000111.")
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the interactive confirmation prompt.",
        )

    def handle(self, *args, **options):
        try:
            phone = normalize_phone_number(options["phone"])
        except serializers.ValidationError as exc:
            raise CommandError(str(exc.detail[0] if isinstance(exc.detail, list) else exc.detail)) from exc

        try:
            user = ApiUser.objects.get(phone=phone)
        except ApiUser.DoesNotExist as exc:
            raise CommandError(f"No account found with phone {phone}.") from exc

        self.stdout.write(f"Found account: id={user.id}, name={user.name}, phone={user.phone}, role={user.role}")

        if not options["yes"]:
            answer = input("Delete this account permanently? Type DELETE to confirm: ")
            if answer != "DELETE":
                self.stdout.write(self.style.WARNING("Cancelled. No account was deleted."))
                return

        deleted_user_id = user.id
        delete_account(user)
        self.stdout.write(self.style.SUCCESS(f"Deleted account id={deleted_user_id}, phone={phone}."))
