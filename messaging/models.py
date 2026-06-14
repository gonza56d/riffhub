"""Direct-message models.

A ``Conversation`` is the canonical 1:1 thread between two users. To keep a
single row per pair (rather than one per direction) the participants are stored
in a fixed order — ``user_low`` always holds the smaller pk and ``user_high``
the larger — so ``(a, b)`` and ``(b, a)`` resolve to the same conversation.
``DirectMessage`` rows hang off it in chronological order.
"""

from django.conf import settings
from django.db import models

from core.models import Moderatable, TimeStampedModel


class Conversation(TimeStampedModel):
    """A canonical 1:1 direct-message thread between two users."""

    user_low = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    user_high = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user_low", "user_high"],
                name="unique_conversation_pair",
            ),
        ]
        ordering = ["-last_message_at"]

    def __str__(self) -> str:
        return f"Conversation #{self.pk}: {self.user_low} ↔ {self.user_high}"

    @classmethod
    def for_pair(cls, a, b) -> tuple["Conversation", bool]:
        """Get (or create) the canonical conversation for two users.

        The pair is ordered so ``user_low`` always has the smaller pk, making
        the lookup direction-independent. Callers guarantee ``a != b``.
        """
        lo, hi = (a, b) if a.pk < b.pk else (b, a)
        return cls.objects.get_or_create(user_low=lo, user_high=hi)

    def other(self, user):
        """Return the other participant relative to ``user``."""
        return self.user_high if user.pk == self.user_low_id else self.user_low

    def involves(self, user) -> bool:
        """Whether ``user`` is one of the two participants."""
        return user.pk in (self.user_low_id, self.user_high_id)


class DirectMessage(TimeStampedModel, Moderatable):
    """A single message posted into a conversation by one of its participants.

    Inherits :class:`~core.models.Moderatable` so a moderator can soft-remove a
    reported message (hidden from the thread but kept, with who/why/when, for an
    auditable and reversible action).
    """

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sent_messages",
    )
    body = models.TextField()
    is_read = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Message from {self.sender} in conversation #{self.conversation_id}"


class ReportStatus(models.TextChoices):
    """Lifecycle of a direct-message report as a moderator handles it."""

    OPEN = "open", "Open"
    DISMISSED = "dismissed", "Dismissed"
    ACTIONED = "actioned", "Actioned"


class DirectMessageReport(TimeStampedModel):
    """A user's report flagging a direct message for moderator review."""

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="dm_reports",
    )
    message = models.ForeignKey(
        "messaging.DirectMessage",
        on_delete=models.CASCADE,
        related_name="reports",
    )
    reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=ReportStatus.choices,
        default=ReportStatus.OPEN,
        db_index=True,
    )
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return (
            f"Report by {self.reporter} on message #{self.message_id} "
            f"({self.get_status_display()})"
        )
