import logging

from django.db import transaction

from .models import MessageAttachment, PartImage


logger = logging.getLogger(__name__)


def collect_account_file_references(user):
    files = []
    seen_names = set()

    def remember(file_field):
        if not file_field:
            return
        name = str(getattr(file_field, "name", "") or "").strip()
        if not name or name in seen_names:
            return
        seen_names.add(name)
        files.append((file_field.storage, name))

    remember(user.avatar)

    for image in PartImage.objects.filter(part_request__requester=user).iterator():
        remember(image.image)

    for attachment in MessageAttachment.objects.filter(message__sender=user).iterator():
        remember(attachment.file)

    return files


def delete_account_files(files):
    for storage, name in files:
        try:
            storage.delete(name)
        except Exception as exc:  # pragma: no cover - cleanup best effort
            logger.warning("Unable to delete uploaded file %s during account removal: %s", name, exc)


def delete_account(user):
    user_id = user.id
    files_to_delete = collect_account_file_references(user)

    with transaction.atomic():
        user.delete()

    delete_account_files(files_to_delete)
    logger.info("Deleted account for user %s.", user_id)
