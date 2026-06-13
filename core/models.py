from decimal import Decimal

from django.core.exceptions import ImproperlyConfigured
from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base adding self-managed created/updated timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SiteConfiguration(TimeStampedModel):
    """Singleton holding runtime-tunable policy values.

    Per PRODUCT.md the promotion thresholds intentionally have **no default**:
    their accessors raise ``ImproperlyConfigured`` until an admin sets them, so
    riffhub can never silently promote a user based on an assumed number.
    """

    # --- Promotion thresholds — MUST be configured (no silent default) ----
    collaborator_promotion_threshold = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Accepted submissions needed to become a Database Collaborator. "
            "Must be set; reading it while unset raises an error."
        ),
    )
    founder_threshold = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Accepted submissions needed for Community Founder (spec suggests "
            "30). Must be set; reading it while unset raises an error."
        ),
    )

    # Founder is toggleable so it can be "closed" once the early era ends,
    # while existing founders keep the badge.
    founder_level_achievable = models.BooleanField(default=True)

    # --- Collab-db acceptance / troll-guard policy ------------------------
    # Tunable knobs governing when a submission is accepted and when a user is
    # throttled for too many rejects. Unlike the promotion thresholds above
    # these MAY have defaults (the spec only forbids silent promotion).
    gear_acceptance_min_net_votes = models.PositiveIntegerField(
        default=3,
        help_text=(
            "Minimum net votes (upvotes − downvotes) a submission needs before "
            "it is auto-published."
        ),
    )
    gear_acceptance_min_voters = models.PositiveIntegerField(
        default=3,
        help_text=(
            "Minimum number of distinct collaborators that must vote before a "
            "submission is auto-published."
        ),
    )
    max_rejected_before_cooldown = models.PositiveIntegerField(
        default=3,
        help_text=(
            "Once a user exceeds this many rejected submissions they are "
            "temporarily blocked from submitting (troll guard, see PRODUCT.md)."
        ),
    )

    # --- Topic / subtopic community-proposal feature ----------------------
    topic_proposals_enabled = models.BooleanField(default=True)
    topic_proposal_voting_days = models.PositiveIntegerField(default=7)
    topic_proposal_pass_ratio = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        default=Decimal("0.750"),
        help_text="Fraction of positive votes required to accept a proposal (0–1).",
    )

    class Meta:
        verbose_name = "Site configuration"

    def __str__(self) -> str:
        return "Site configuration"

    # --- Singleton plumbing ------------------------------------------------
    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls) -> "SiteConfiguration":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    # --- Guarded accessors (no silent defaults) ---------------------------
    @property
    def collaborator_threshold(self) -> int:
        if self.collaborator_promotion_threshold is None:
            raise ImproperlyConfigured(
                "SiteConfiguration.collaborator_promotion_threshold is not set. "
                "Configure it in the admin before promoting collaborators."
            )
        return self.collaborator_promotion_threshold

    @property
    def founder_promotion_threshold(self) -> int:
        if self.founder_threshold is None:
            raise ImproperlyConfigured(
                "SiteConfiguration.founder_threshold is not set. "
                "Configure it in the admin before awarding the Founder level."
            )
        return self.founder_threshold
