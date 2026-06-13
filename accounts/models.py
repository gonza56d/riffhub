import uuid

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class Level(models.IntegerChoices):
    """Ordered user levels (see PRODUCT.md "User levels").

    Backed by ``IntegerChoices`` so levels are directly comparable: a higher
    value strictly outranks a lower one, which is what ``is_at_least`` relies
    on. Gaps are intentional, leaving room to insert future tiers.
    """

    ANONYMOUS = 0, "Anonymous"
    REGULAR = 10, "Regular"
    COLLABORATOR = 20, "Database Collaborator"
    FOUNDER = 30, "Community Founder"
    MODERATOR = 40, "Community Moderator"
    CREATOR = 50, "Riffhub Creator"


class User(AbstractUser):
    """Riffhub user account.

    Extends Django's ``AbstractUser`` so we keep username-based identity (the
    public handle shown on the forum) while making e-mail unique and tracking
    confirmation — confirming e-mail is what unlocks collab-db submissions
    (see PRODUCT.md).

    Reputation reflects how participative/collaborative a user is. The *derived*
    levels (Database Collaborator / Community Founder) are computed from
    accepted submissions against the configured thresholds, while the *granted*
    roles (Community Moderator / Riffhub Creator) and the sticky Founder badge
    are explicit flags.
    """

    email = models.EmailField(unique=True)
    email_confirmed = models.BooleanField(default=False)

    # --- Reputation & submission tallies ----------------------------------
    reputation_score = models.IntegerField(default=0, db_index=True)
    accepted_submissions_count = models.PositiveIntegerField(default=0)
    rejected_submissions_count = models.PositiveIntegerField(default=0)

    # --- Role / badge flags ------------------------------------------------
    # Founder is a STICKY badge: set once earned and never auto-removed, so
    # early collaborators keep the recognition even after the level is closed.
    is_founder = models.BooleanField(default=False)
    is_community_moderator = models.BooleanField(default=False)
    is_riffhub_creator = models.BooleanField(default=False)

    def __str__(self) -> str:
        return self.username

    # --- Derived level -----------------------------------------------------
    @property
    def level(self) -> "Level":
        """Highest level this user currently qualifies for.

        Granted roles win first, then the sticky Founder badge, then the
        config-driven Collaborator promotion, otherwise Regular.

        The collaborator-threshold read is wrapped in ``try/except`` because
        ``SiteConfiguration`` raises ``ImproperlyConfigured`` while the
        threshold is unset. That is the intended safety net — nobody is
        promoted without explicit config — so an unset threshold must mean
        "not promoted", never a crash.
        """
        if self.is_riffhub_creator:
            return Level.CREATOR
        if self.is_community_moderator:
            return Level.MODERATOR
        if self.is_founder:
            return Level.FOUNDER

        from core.models import SiteConfiguration

        try:
            threshold = SiteConfiguration.get_solo().collaborator_threshold
        except ImproperlyConfigured:
            return Level.REGULAR
        if self.accepted_submissions_count >= threshold:
            return Level.COLLABORATOR
        return Level.REGULAR

    def is_at_least(self, level: "Level") -> bool:
        """Whether this user's level meets or exceeds ``level``."""
        return self.level >= level

    def add_reputation(self, amount: int) -> None:
        """Adjust the reputation score by ``amount`` (may be negative) and save.

        The forum domain calls this when a user's post/comment is up/downvoted.
        """
        self.reputation_score = (self.reputation_score or 0) + amount
        self.save(update_fields=["reputation_score"])


class EmailConfirmation(TimeStampedModel):
    """A single e-mail confirmation token for a user.

    Confirming the e-mail is what unlocks collab-db submissions. Actual e-mail
    delivery is intentionally out of scope here — only the token/lifecycle is
    modelled.
    """

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="email_confirmations",
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        state = "confirmed" if self.confirmed_at else "pending"
        return f"Email confirmation for {self.user} ({state})"

    def confirm(self, *, save: bool = True) -> None:
        """Mark this token used and flag the owning user's e-mail confirmed."""
        self.confirmed_at = timezone.now()
        self.user.email_confirmed = True
        if save:
            self.user.save(update_fields=["email_confirmed"])
            self.save(update_fields=["confirmed_at", "updated_at"])
