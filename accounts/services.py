"""Account standing services.

Pure functions that recompute a user's collab-db standing from the source of
truth (their accepted catalog submissions) and award the sticky Founder badge.
Kept out of the model so the rules stay easy to find and call from the catalog
review workflow.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from catalog.constants import PublicationStatus

# Reverse relations on the user pointing at each catalog entry type's
# ``submitted_by`` FK. Defined by ``CatalogEntry`` as ``%(class)s_submissions``.
SUBMISSION_RELATIONS = (
    "brand_submissions",
    "bridge_submissions",
    "pickup_submissions",
    "tuner_submissions",
    "nut_submissions",
    "guitarmodel_submissions",
)


def count_accepted_submissions(user) -> int:
    """Count this user's submissions that have reached ``PUBLISHED`` across
    every catalog entry type."""
    total = 0
    for relation in SUBMISSION_RELATIONS:
        manager = getattr(user, relation)
        total += manager.filter(status=PublicationStatus.PUBLISHED).count()
    return total


def recompute_standing(user) -> None:
    """Recompute ``accepted_submissions_count`` and award the Founder badge.

    Recounts accepted submissions from the catalog (the source of truth) and,
    if the user newly qualifies for Founder *and* the level is still
    achievable, sets the sticky ``is_founder`` flag. The badge is never unset
    here: once earned it stays, even if the user later drops below the
    threshold or the level is closed.
    """
    from core.models import SiteConfiguration

    accepted = count_accepted_submissions(user)
    update_fields = ["accepted_submissions_count"]
    user.accepted_submissions_count = accepted

    if not user.is_founder:
        config = SiteConfiguration.get_solo()
        try:
            founder_threshold = config.founder_promotion_threshold
        except ImproperlyConfigured:
            founder_threshold = None
        if (
            founder_threshold is not None
            and config.founder_level_achievable
            and accepted >= founder_threshold
        ):
            user.is_founder = True
            update_fields.append("is_founder")

    user.save(update_fields=update_fields)


def email_confirmation_required() -> bool:
    """Whether new users must confirm their e-mail before they can contribute.

    Mirrors the ``REQUIRE_EMAIL_CONFIRMATION`` setting (env-toggleable, default
    on). When off, sign-up auto-confirms and no confirmation e-mail is sent —
    the feature's code stays in place, just dormant.
    """
    return settings.REQUIRE_EMAIL_CONFIRMATION


def has_confirmed_email(user) -> bool:
    """True if ``user`` has cleared the e-mail gate — either they confirmed, or
    e-mail confirmation is currently disabled."""
    return user.email_confirmed or not email_confirmation_required()
