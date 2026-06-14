from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Count, Sum

from catalog.constants import CorrectionStatus, VoteValue
from core.models import TimeStampedModel


class ReviewVote(TimeStampedModel):
    """A +1/−1 vote a collaborator casts on a submitted catalog entry.

    The target is any ``CatalogEntry`` subclass, reached through a generic
    relation so a single table covers brands, gear and guitars. A user may hold
    at most one vote per target (enforced by the unique constraint); casting the
    opposite value toggles the existing one (see ``catalog.services``).
    """

    voter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="review_votes",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    value = models.SmallIntegerField(choices=VoteValue.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["voter", "content_type", "object_id"],
                name="unique_review_vote_per_voter_target",
            )
        ]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.voter} {self.get_value_display()} on {self.target}"

    # --- Tally helpers -----------------------------------------------------
    @classmethod
    def _for_target(cls, target) -> models.QuerySet:
        return cls.objects.filter(
            content_type=ContentType.objects.get_for_model(target),
            object_id=target.pk,
        )

    @classmethod
    def net_votes(cls, target) -> int:
        """Sum of vote values (upvotes − downvotes) on ``target``."""
        return cls._for_target(target).aggregate(total=Sum("value"))["total"] or 0

    @classmethod
    def voter_count(cls, target) -> int:
        """Number of distinct voters who have voted on ``target``."""
        return (
            cls._for_target(target)
            .aggregate(n=Count("voter", distinct=True))["n"]
            or 0
        )


class Correction(TimeStampedModel):
    """A proposed fix to a catalog entry, filed by a collaborator.

    Lets the community repair wrong specs (per PRODUCT.md) without silently
    overwriting them: a correction describes the change in ``body`` and is then
    applied or rejected by a reviewer. The target is any ``CatalogEntry``
    subclass via a generic relation.
    """

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="corrections",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    body = models.TextField(help_text="Describes the proposed correction.")
    status = models.CharField(
        max_length=20,
        choices=CorrectionStatus.choices,
        default=CorrectionStatus.OPEN,
        db_index=True,
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_corrections",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"Correction for {self.target} ({self.get_status_display()})"
