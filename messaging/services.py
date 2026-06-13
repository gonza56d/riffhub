"""Direct-message business rules.

Views resolve targets and choose HTML; the rules — who may DM, what a valid
message is, and how unread state is tracked — live here. Sending honours the
same ``moderation.can_participate`` gate as posting/commenting, so a banned or
silenced user cannot send DMs (PRODUCT.md).
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.utils import timezone

from moderation.services import can_participate

from messaging.models import Conversation, DirectMessage


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
        messages = conversation.messages.all()
        last_message = messages.last()
        unread = (
            conversation.messages.filter(is_read=False)
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
