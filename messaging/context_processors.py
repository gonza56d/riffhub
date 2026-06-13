"""Template context for messaging.

Exposes the viewer's unread-DM badge count to every template so the global nav
can render it without each view having to pass it explicitly.
"""

from messaging.services import unread_count


def messaging_flags(request) -> dict:
    user = request.user
    return {
        "unread_dm_count": unread_count(user) if user.is_authenticated else 0,
    }
