"""Collab-db review workflow services.

The behaviour PRODUCT.md asks for around submission voting, acceptance and the
troll guard lives here as plain functions so the rules are in one place and can
be called from views, the admin or future tasks.
"""

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch
from django.utils import timezone

from accounts.models import Level
from accounts.services import recompute_standing
from catalog.constants import REP_ACCEPTED_SUBMISSION, PublicationStatus, VoteValue
from catalog.models import Brand, Bridge, CatalogComment, GuitarModel, Nut, Pickup, Tuner
from catalog.models.review import ReviewVote
from core.models import SiteConfiguration
from moderation.services import can_participate


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

    Only an UNDER_REVISION entry can be evaluated: once an entry has been
    published or rejected it is final, so re-running the evaluator (e.g. on a
    late/toggled vote) can neither re-award reputation on a published entry nor
    resurrect a moderator-rejected one.
    """
    if entry.status != PublicationStatus.UNDER_REVISION:
        return False

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

    Gate (PRODUCT.md): the e-mail must be confirmed (unless e-mail confirmation
    is disabled via REQUIRE_EMAIL_CONFIRMATION), and a user who has racked up too
    many rejected submissions is temporarily blocked (troll guard).
    """
    from accounts.services import has_confirmed_email

    if not has_confirmed_email(user):
        return False
    config = SiteConfiguration.get_solo()
    if user.rejected_submissions_count > config.max_rejected_before_cooldown:
        return False
    return True


# ---------------------------------------------------------------------------
# Catalog comments (guitar + gear detail pages)
# ---------------------------------------------------------------------------
def _comment_ct(target):
    """ContentType for attaching/looking up a comment on a catalog object.

    The read and write paths must resolve the ContentType identically, so this
    is the single source of truth for both.
    """
    return ContentType.objects.get_for_model(target, for_concrete_model=False)


def add_catalog_comment(*, target, author, body, parent=None):
    """Create a comment (or one-level reply) on a catalog object.

    Gated by ``moderation.can_participate`` (silenced/banned users are blocked,
    like forum posting). A blank body is rejected. A reply (``parent`` given)
    must hang off a *top-level* comment on the *same* target — replying to a
    reply, or to a comment from another page, is rejected.
    """
    if not can_participate(author):
        raise PermissionDenied("You can't comment while silenced or banned.")
    body = (body or "").strip()
    if not body:
        raise ValidationError("A comment can't be empty.")

    ct = _comment_ct(target)
    if parent is not None:
        if parent.parent_id is not None:
            raise ValidationError("You can only reply to a top-level comment.")
        if parent.content_type_id != ct.id or parent.object_id != target.pk:
            raise ValidationError("That comment belongs to a different page.")

    return CatalogComment.objects.create(
        content_type=ct,
        object_id=target.pk,
        author=author,
        body=body,
        parent=parent,
    )


def catalog_comment_thread(target):
    """Top-level, non-removed comments for ``target`` (newest first), each with
    its non-removed replies prefetched (oldest first). The caller paginates the
    returned (top-level) queryset.
    """
    ct = _comment_ct(target)
    visible_replies = CatalogComment.objects.filter(is_removed=False).select_related(
        "author"
    )
    return (
        CatalogComment.objects.filter(
            content_type=ct,
            object_id=target.pk,
            parent__isnull=True,
            is_removed=False,
        )
        .select_related("author")
        .prefetch_related(Prefetch("replies", queryset=visible_replies))
        .order_by("-created_at")
    )


def delete_catalog_comment(user, comment):
    """Author-delete a catalog comment or reply (soft delete).

    Mirrors the forum's ``delete_comment``: only the author may delete; the
    comment is then replaced by a "This message was deleted." placeholder for
    everyone, while the row is kept so moderators / Creators can still audit and
    reveal the original. A deleted comment keeps its replies (they stay visible).
    Idempotent if already deleted.
    """
    if not getattr(user, "is_authenticated", False) or comment.author_id != user.pk:
        raise PermissionDenied("You can only delete your own comments.")
    if comment.is_deleted:
        return comment
    comment.mark_deleted(by=user)
    return comment
