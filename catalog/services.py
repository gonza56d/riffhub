"""Collab-db review workflow services.

The behaviour PRODUCT.md asks for around submission voting, acceptance and the
troll guard lives here as plain functions so the rules are in one place and can
be called from views, the admin or future tasks.
"""

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from accounts.models import Level
from accounts.services import recompute_standing
from catalog.constants import REP_ACCEPTED_SUBMISSION, PublicationStatus, VoteValue
from catalog.models import Brand, Bridge, GuitarModel, Nut, Pickup, Tuner
from catalog.models.review import ReviewVote
from core.models import SiteConfiguration


def cast_review_vote(user, target, value: int) -> ReviewVote | None:
    """Cast (or toggle) ``user``'s +1/−1 review vote on ``target``.

    Permission rules (PRODUCT.md):
      * only Database Collaborators and above may review submissions,
      * a user may not vote on their own submission.
    Both violations raise ``PermissionError``.

    Toggle semantics mirror the forum's: re-casting the same value removes the
    vote (returns ``None``); casting the opposite value switches it.
    """
    if not user.is_at_least(Level.COLLABORATOR):
        raise PermissionError(
            "Only Database Collaborators and above can vote on submissions."
        )
    if getattr(target, "submitted_by_id", None) == user.pk:
        raise PermissionError("You cannot vote on your own submission.")

    value = VoteValue(value)
    content_type = ContentType.objects.get_for_model(target)
    existing = ReviewVote.objects.filter(
        voter=user, content_type=content_type, object_id=target.pk
    ).first()

    if existing is None:
        return ReviewVote.objects.create(
            voter=user,
            content_type=content_type,
            object_id=target.pk,
            value=value,
        )
    if existing.value == value:
        # Re-casting the same vote toggles it off.
        existing.delete()
        return None
    existing.value = value
    existing.save(update_fields=["value", "updated_at"])
    return existing


def evaluate_submission(entry) -> bool:
    """Publish ``entry`` if its votes clear the configured acceptance bar.

    Returns ``True`` when the entry is (now) accepted. Acceptance needs both a
    net-vote floor and a distinct-voter floor so a single enthusiastic voter
    cannot wave something through. On acceptance we publish, credit the
    submitter and recompute their standing (which may promote them).
    """
    config = SiteConfiguration.get_solo()
    net = ReviewVote.net_votes(entry)
    voters = ReviewVote.voter_count(entry)

    if (
        net >= config.gear_acceptance_min_net_votes
        and voters >= config.gear_acceptance_min_voters
    ):
        entry.mark_published()
        submitter = entry.submitted_by
        if submitter is not None:
            submitter.accepted_submissions_count += 1
            submitter.save(update_fields=["accepted_submissions_count"])
            # recompute_standing re-derives the count from the catalog (source
            # of truth) and may award the sticky Founder badge.
            recompute_standing(submitter)
            # Accepted contributions also build reputation (PRODUCT.md: the
            # score rewards uploading new gear, not just forum participation).
            submitter.add_reputation(REP_ACCEPTED_SUBMISSION)
        return True

    # Below the bar: we do NOT auto-reject here, so a temporarily negative
    # score can still recover. Explicit rejection goes through
    # ``reject_submission`` (e.g. a moderator, or a strong negative consensus).
    return False


def sweep_pending_submissions() -> dict:
    """Re-evaluate every UNDER_REVISION entry across all collab-db types.

    Intended for a scheduled task: walks the six catalog types (``Brand``,
    ``Bridge``, ``Pickup``, ``Tuner``, ``Nut``, ``GuitarModel``), and runs
    :func:`evaluate_submission` on each entry still awaiting review. Each call
    is wrapped in its own ``try/except`` so one bad entry can't abort the sweep.

    Returns a tally ``{"evaluated": n, "published": p}`` where ``published``
    counts the entries :func:`evaluate_submission` cleared this run.
    """
    evaluated = published = 0
    for model in (Brand, Bridge, Pickup, Tuner, Nut, GuitarModel):
        for entry in model.objects.under_revision():
            evaluated += 1
            try:
                if evaluate_submission(entry):
                    published += 1
            except Exception:
                continue

    return {"evaluated": evaluated, "published": published}


def reject_submission(entry, *, by=None) -> None:
    """Reject a submission: mark it rejected and tick the submitter's reject
    counter — the value the troll guard in ``can_submit_to_collab`` reads."""
    entry.status = PublicationStatus.REJECTED
    entry.reviewed_at = timezone.now()
    entry.save(update_fields=["status", "reviewed_at", "updated_at"])
    submitter = entry.submitted_by
    if submitter is not None:
        submitter.rejected_submissions_count += 1
        submitter.save(update_fields=["rejected_submissions_count"])
        recompute_standing(submitter)


def can_submit_to_collab(user) -> bool:
    """Whether ``user`` may currently submit to the collab-db.

    Gate (PRODUCT.md): the e-mail must be confirmed, and a user who has racked
    up too many rejected submissions is temporarily blocked (troll guard).
    """
    if not user.email_confirmed:
        return False
    config = SiteConfiguration.get_solo()
    if user.rejected_submissions_count > config.max_rejected_before_cooldown:
        return False
    return True
