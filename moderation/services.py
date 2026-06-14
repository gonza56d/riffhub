"""Moderation rules (PRODUCT.md).

Free speech is the default — rudeness/insults are not moderated. These actions
target *unrelated/illegal* content and threats:
  * move    — recategorise a mis-filed (but acceptable) post
  * remove  — soft-delete off-topic content (kept for audit/restore)
  * warn    — record a warning to a user
  * silence — counted, escalating mute (1 week -> 1 month -> permanent); a
              silenced user cannot post, comment or DM
  * ban     — deactivate the account; moderators cannot ban Creators, and only
              Creators may ban Moderators
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts.models import Level
from moderation.constants import SILENCE_DURATIONS
from moderation.models import Ban, ContentAction, Silence, Warning
from moderation.constants import ContentActionType


def _require_moderator(user) -> None:
    if not (getattr(user, "is_authenticated", False) and user.is_at_least(Level.MODERATOR)):
        raise PermissionDenied("Moderator privileges are required.")


def _assert_can_sanction(actor, target) -> None:
    """Authority check shared by silence/ban/lift_ban (PRODUCT.md).

    Nobody sanctions themselves; Riffhub Creators cannot be sanctioned at all
    (this mirrors the original ``ban`` rule — even another Creator can't); only a
    Riffhub Creator may sanction a Community Moderator.
    """
    if target.pk == actor.pk:
        raise PermissionDenied("You cannot sanction yourself.")
    if target.is_at_least(Level.CREATOR):
        raise PermissionDenied("Riffhub Creators cannot be sanctioned.")
    if target.is_at_least(Level.MODERATOR) and not actor.is_at_least(Level.CREATOR):
        raise PermissionDenied("Only a Riffhub Creator can sanction a Community Moderator.")


# --- enforcement queries ---------------------------------------------------
def is_banned(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if not user.is_active:
        return True
    return Ban.objects.filter(target=user, lifted_at__isnull=True).exists()


def active_silence(user):
    """Return the user's current active Silence, or None."""
    if not getattr(user, "is_authenticated", False):
        return None
    for silence in user.silences.all():
        if silence.is_active:
            return silence
    return None


def can_participate(user) -> bool:
    """Whether the user may post / comment / DM (not banned, not silenced)."""
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if is_banned(user):
        return False
    return active_silence(user) is None


# --- user actions ----------------------------------------------------------
def warn(moderator, target, reason, content=None) -> Warning:
    _require_moderator(moderator)
    warning = Warning(target=target, issued_by=moderator, reason=reason)
    if content is not None:
        warning.content = content
    warning.save()
    return warning


def silence(moderator, target, reason) -> Silence:
    _require_moderator(moderator)
    _assert_can_sanction(moderator, target)

    sequence = target.silences.count() + 1
    now = timezone.now()
    duration = SILENCE_DURATIONS.get(sequence)
    permanent = duration is None  # 3rd and beyond
    return Silence.objects.create(
        target=target,
        issued_by=moderator,
        reason=reason,
        sequence=sequence,
        starts_at=now,
        ends_at=None if permanent else now + duration,
        is_permanent=permanent,
        is_public_flag=permanent,
    )


def ban(moderator, target, reason) -> Ban:
    _require_moderator(moderator)
    _assert_can_sanction(moderator, target)

    existing = Ban.objects.filter(target=target, lifted_at__isnull=True).first()
    if existing is not None:
        return existing

    ban = Ban.objects.create(target=target, issued_by=moderator, reason=reason)
    target.is_active = False
    target.save(update_fields=["is_active"])
    return ban


def lift_ban(moderator, target) -> None:
    _require_moderator(moderator)
    _assert_can_sanction(moderator, target)
    lifted = Ban.objects.filter(target=target, lifted_at__isnull=True).update(
        lifted_at=timezone.now()
    )
    # Only reactivate if we actually lifted an active ban — don't silently
    # re-enable an account that was deactivated for some other reason.
    if lifted:
        target.is_active = True
        target.save(update_fields=["is_active"])


# --- content actions -------------------------------------------------------
def move_content(moderator, post, to_subtopic, reason="") -> None:
    _require_moderator(moderator)
    # A move must not leave the post in a state Post.clean() forbids: a price
    # only belongs in the Gear Market. Reject a move that would create an
    # invalid listing (priceless in the market, or priced outside it) rather
    # than silently bypassing the coupling via update_fields.
    to_is_market = to_subtopic.topic.is_market
    if to_is_market and post.price is None:
        raise ValidationError(
            "Can't move a post without a price into the Gear Market."
        )
    if not to_is_market and post.price is not None:
        raise ValidationError(
            "Can't move a priced Gear Market listing out of the market."
        )
    from_subtopic = post.subtopic
    post.subtopic = to_subtopic
    post.save(update_fields=["subtopic"])
    ContentAction.objects.create(
        moderator=moderator,
        action=ContentActionType.MOVE,
        content=post,
        reason=reason,
        detail={"from": str(from_subtopic), "to": str(to_subtopic)},
    )


def remove_content(moderator, obj, reason="") -> None:
    _require_moderator(moderator)
    # Re-removing already-removed content would overwrite the original
    # who/why/when and stack a duplicate audit row — the soft-delete record is
    # meant to preserve the first removal. No-op if already removed.
    if obj.is_removed:
        return
    obj.mark_removed(by=moderator, reason=reason)
    ContentAction.objects.create(
        moderator=moderator, action=ContentActionType.REMOVE, content=obj, reason=reason
    )


def restore_content(moderator, obj) -> None:
    _require_moderator(moderator)
    # Restoring content that was never removed is a no-op — don't pollute the
    # audit trail with a RESTORE that restored nothing.
    if not obj.is_removed:
        return
    obj.restore()
    ContentAction.objects.create(
        moderator=moderator, action=ContentActionType.RESTORE, content=obj
    )
