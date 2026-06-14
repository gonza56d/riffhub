"""Direct-message business rules.

Views resolve targets and choose HTML; the rules — who may DM, what a valid
message is, and how unread state is tracked — live here. Sending honours the
same ``moderation.can_participate`` gate as posting/commenting, so a banned or
silenced user cannot send DMs (PRODUCT.md).
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.utils import timezone

from accounts.models import Level
from moderation.services import can_participate

from messaging.models import (
    Conversation,
    DirectMessage,
    DirectMessageReport,
    ReportStatus,
)


def get_conversation(a, b) -> Conversation:
    """Return the canonical conversation between two users (created if needed)."""
    conversation, _ = Conversation.for_pair(a, b)
    return conversation


def send_message(sender, recipient, body) -> DirectMessage:
    """Create and store a direct message from ``sender`` to ``recipient``.

    Raises ``PermissionDenied`` if the sender is messaging themselves or is not
    allowed to participate (banned/silenced), and ``ValidationError`` if the
    body is blank/whitespace-only.
    """
    if sender == recipient or not can_participate(sender):
        raise PermissionDenied("You can't send this message.")
    if not (body or "").strip():
        raise ValidationError("A message can't be empty.")

    conversation = get_conversation(sender, recipient)
    message = DirectMessage.objects.create(
        conversation=conversation,
        sender=sender,
        body=body,
        is_read=False,
    )
    conversation.last_message_at = timezone.now()
    conversation.save(update_fields=["last_message_at", "updated_at"])
    return message


def mark_read(user, conversation) -> None:
    """Mark every message the other participant sent in ``conversation`` read."""
    if not conversation.involves(user):
        return
    conversation.messages.filter(is_read=False).exclude(sender=user).update(
        is_read=True
    )


def unread_count(user) -> int:
    """Count messages awaiting ``user`` across all their conversations.

    Returns 0 for anonymous/unauthenticated users.
    """
    if not getattr(user, "is_authenticated", False):
        return 0
    return (
        DirectMessage.objects.filter(
            conversation__in=Conversation.objects.filter(
                Q(user_low=user) | Q(user_high=user)
            ),
            is_read=False,
            is_removed=False,
        )
        .exclude(sender=user)
        .count()
    )


def inbox_rows(user) -> list[dict]:
    """Build one display row per conversation involving ``user``.

    Ordered by most-recent activity. Each row carries the conversation, the
    other participant, the latest message (or ``None``), and that thread's
    unread count for ``user``.
    """
    conversations = (
        Conversation.objects.filter(Q(user_low=user) | Q(user_high=user))
        .select_related("user_low", "user_high")
        .order_by("-last_message_at")
    )
    rows: list[dict] = []
    for conversation in conversations:
        # Prefer the most recent non-removed message for the preview so a
        # moderator-removed body never surfaces in the inbox; fall back to the
        # latest message (rendered as a placeholder by the template) when every
        # message is removed.
        last_message = (
            conversation.messages.filter(is_removed=False).last()
            or conversation.messages.last()
        )
        unread = (
            conversation.messages.filter(is_read=False, is_removed=False)
            .exclude(sender=user)
            .count()
        )
        rows.append(
            {
                "conversation": conversation,
                "other": conversation.other(user),
                "last_message": last_message,
                "unread": unread,
            }
        )
    return rows


def report_message(reporter, message, reason) -> DirectMessageReport:
    """File a report flagging ``message`` for moderator review.

    Only a participant in the message's conversation may report, and never
    their own message. An already-removed message can't be reported (it has
    already been handled), and reporting the same message twice while a prior
    report is still ``OPEN`` is a no-op that returns the existing report.

    Raises ``PermissionDenied`` if ``reporter`` is not a participant, is the
    sender, or the message is already removed, and ``ValidationError`` if
    ``reason`` is blank/whitespace-only.
    """
    if (
        not message.conversation.involves(reporter)
        or reporter == message.sender
        or message.is_removed
    ):
        raise PermissionDenied("You can't report this message.")
    if not (reason or "").strip():
        raise ValidationError("A report needs a reason.")

    existing = DirectMessageReport.objects.filter(
        reporter=reporter,
        message=message,
        status=ReportStatus.OPEN,
    ).first()
    if existing is not None:
        return existing

    return DirectMessageReport.objects.create(
        reporter=reporter,
        message=message,
        reason=reason,
        status=ReportStatus.OPEN,
    )


def open_reports(viewer=None):
    """Return unresolved reports (newest first) for the moderation queue.

    A report filed *against a moderator* (the reported message's sender is a
    moderator or above) is visible only to Riffhub Creators — a moderator must
    never see a report about themselves or a peer moderator. Pass ``viewer`` to
    apply that visibility filter; omit it for the unfiltered queue.
    """
    reports = list(
        DirectMessageReport.objects.filter(status=ReportStatus.OPEN)
        .select_related("message__sender", "message__conversation", "reporter")
        .order_by("-created_at")
    )
    if viewer is not None and not viewer.is_at_least(Level.CREATOR):
        reports = [r for r in reports if not _is_against_moderator(r)]
    return reports


def _require_moderator(user) -> None:
    """Raise ``PermissionDenied`` unless ``user`` is at least a moderator."""
    if not (
        getattr(user, "is_authenticated", False)
        and user.is_at_least(Level.MODERATOR)
    ):
        raise PermissionDenied("Moderator privileges are required.")


def _is_against_moderator(report) -> bool:
    """True when the reported message was sent by a moderator-or-above — i.e. a
    report *against* a moderator, which only Riffhub Creators may see or handle.
    """
    return report.message.sender.is_at_least(Level.MODERATOR)


def _require_can_handle(actor, report) -> None:
    """Gate report resolution. Moderators handle ordinary reports, but a report
    against a moderator is reserved for Riffhub Creators, so a moderator can
    neither act on a report about themselves nor on one about a peer moderator.
    """
    _require_moderator(actor)
    if _is_against_moderator(report) and not actor.is_at_least(Level.CREATOR):
        raise PermissionDenied(
            "Only a Riffhub Creator can handle a report against a moderator."
        )


def dismiss_report(moderator, report) -> None:
    """Close ``report`` as dismissed, recording who handled it and when.

    A no-op if the report has already been handled, so a resolved report can't
    be flipped into a state that contradicts the message's removal.
    """
    _require_can_handle(moderator, report)
    if report.status != ReportStatus.OPEN:
        return
    report.status = ReportStatus.DISMISSED
    report.handled_by = moderator
    report.handled_at = timezone.now()
    report.save(update_fields=["status", "handled_by", "handled_at", "updated_at"])


def remove_reported_message(moderator, report, reason="") -> None:
    """Soft-remove the reported message and mark ``report`` as actioned.

    A no-op if the report has already been handled, so a resolved report can't
    be re-actioned.
    """
    _require_can_handle(moderator, report)
    if report.status != ReportStatus.OPEN:
        return
    report.message.mark_removed(by=moderator, reason=reason)
    report.status = ReportStatus.ACTIONED
    report.handled_by = moderator
    report.handled_at = timezone.now()
    report.save(update_fields=["status", "handled_by", "handled_at", "updated_at"])
