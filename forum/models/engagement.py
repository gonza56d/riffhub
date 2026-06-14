import io

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models

from core.models import TimeStampedModel

from forum.constants import (
    ALLOWED_IMAGE_FORMATS,
    ATTACHMENT_UPLOAD_DIR,
    MAX_IMAGE_HEIGHT,
    MAX_IMAGE_SIZE_BYTES,
    MAX_IMAGE_WIDTH,
    VoteValue,
)


class Vote(TimeStampedModel):
    """An up/down vote on a Post or Comment (generic relation).

    PRODUCT.md: up and down are mutually exclusive, you cannot vote your own
    content, and re-casting the same value removes the vote. Those rules live
    in ``forum.services.cast_vote``; the model only guarantees one row per
    (voter, target) via the unique constraint. Positives and negatives are
    counted separately by ``vote_tally``.
    """

    voter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="forum_votes",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    value = models.IntegerField(choices=VoteValue.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["voter", "content_type", "object_id"],
                name="unique_vote_per_voter_per_target",
            )
        ]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self) -> str:
        return f"{self.voter} {self.get_value_display()} on {self.target}"


class Reaction(TimeStampedModel):
    """An emoji reaction on a Post or Comment (generic relation).

    PRODUCT.md: a user may react with as many *different* emojis as they like
    but only one of each type per target, and may not react to their own
    content. Clicking the same emoji again removes it. The "one of each type"
    rule is enforced by the unique constraint; the rest lives in
    ``forum.services.toggle_reaction``.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="forum_reactions",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    emoji = models.CharField(max_length=32)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "content_type", "object_id", "emoji"],
                name="unique_reaction_per_user_target_emoji",
            )
        ]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self) -> str:
        return f"{self.user} reacted {self.emoji} to {self.target}"


def validate_forum_image(image) -> None:
    """Validate an uploaded forum image with Pillow.

    Confirms the file is really an image of an allowed format and enforces the
    documented size/dimension caps (see ``forum.constants``). Reused by both
    the model field validator and :meth:`Attachment.clean`. Raises
    ``ValidationError`` on any problem.
    """
    # Size guard first — cheap, and avoids decoding an enormous file.
    size = getattr(image, "size", None)
    if size is not None and size > MAX_IMAGE_SIZE_BYTES:
        max_mib = MAX_IMAGE_SIZE_BYTES // (1024 * 1024)
        raise ValidationError(
            f"Image is too large (max {max_mib} MiB)."
        )

    # Pillow is the project's image library (filesystem storage decision).
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a project dependency
        return

    try:
        image.seek(0)
        data = image.read()
        # verify() checks integrity but leaves the file unusable afterwards,
        # so re-open from the buffered bytes to read the real dimensions.
        Image.open(io.BytesIO(data)).verify()
        reopened = Image.open(io.BytesIO(data))
        image_format = reopened.format
        width, height = reopened.size
    except Exception as exc:  # noqa: BLE001 - any Pillow failure = not an image
        raise ValidationError("Upload a valid image file.") from exc
    finally:
        try:
            image.seek(0)
        except (ValueError, OSError):
            pass

    if image_format not in ALLOWED_IMAGE_FORMATS:
        allowed = ", ".join(ALLOWED_IMAGE_FORMATS)
        raise ValidationError(f"Unsupported image format (allowed: {allowed}).")

    if width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT:
        raise ValidationError(
            f"Image is too large (max {MAX_IMAGE_WIDTH}x{MAX_IMAGE_HEIGHT} px)."
        )


class Attachment(TimeStampedModel):
    """An image attached to a Post or Comment (generic relation).

    Project decision (PRODUCT.md): images are stored on disk via ``ImageField``
    — never DB blobs — and validated with Pillow. Videos are *not* uploaded;
    they are external links on the post/comment (``video_url``).
    """

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="forum_attachments",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    image = models.ImageField(
        upload_to=ATTACHMENT_UPLOAD_DIR, validators=[validate_forum_image]
    )
    caption = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self) -> str:
        return f"Attachment on {self.target}"

    def clean(self) -> None:
        """Re-run image validation at the model level (admin/forms)."""
        super().clean()
        if self.image:
            validate_forum_image(self.image)
