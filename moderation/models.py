from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from moderation.constants import ContentActionType


class Warning(TimeStampedModel):
    """A moderator's recorded warning to a user (optionally about some content)."""

    target = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="warnings"
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    reason = models.TextField()
    content_type = models.ForeignKey(
        ContentType, on_delete=models.SET_NULL, null=True, blank=True
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    content = GenericForeignKey("content_type", "object_id")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Warning to {self.target_id}"


class Silence(TimeStampedModel):
    """A counted silence. Silenced users cannot post, comment, or DM.

    Sequence drives the escalation (1 week -> 1 month -> permanent); permanent
    silences are publicly flagged so everyone can see the user is muted.
    """

    target = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="silences"
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    reason = models.TextField()
    sequence = models.PositiveSmallIntegerField(
        help_text="1st, 2nd, 3rd... silence for this user."
    )
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Null = permanent.")
    is_permanent = models.BooleanField(default=False)
    is_public_flag = models.BooleanField(
        default=False, help_text="Permanent silences are publicly flagged."
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        kind = "permanent" if self.is_permanent else f"until {self.ends_at:%Y-%m-%d}"
        return f"Silence #{self.sequence} of {self.target_id} ({kind})"

    @property
    def is_active(self) -> bool:
        if self.is_permanent:
            return True
        return self.ends_at is not None and self.ends_at > timezone.now()


class Ban(TimeStampedModel):
    """A ban. The user's account is deactivated; can be lifted."""

    target = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bans"
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    reason = models.TextField()
    lifted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Ban of {self.target_id}" + ("" if self.is_active else " (lifted)")

    @property
    def is_active(self) -> bool:
        return self.lifted_at is None


class ContentAction(TimeStampedModel):
    """Audit trail of moderator actions on a post/comment (move / remove / restore)."""

    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    action = models.CharField(max_length=20, choices=ContentActionType.choices)
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True)
    object_id = models.PositiveIntegerField(null=True)
    content = GenericForeignKey("content_type", "object_id")
    reason = models.TextField(blank=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_action_display()} ({self.content_type_id}:{self.object_id})"
