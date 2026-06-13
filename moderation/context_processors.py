from accounts.models import Level


def moderation_flags(request):
    """Expose ``is_moderator`` / ``is_creator`` to every template so nav links
    and inline controls can show conditionally without each view passing them."""
    user = getattr(request, "user", None)
    authed = bool(user and user.is_authenticated)
    return {
        "is_moderator": bool(authed and user.is_at_least(Level.MODERATOR)),
        "is_creator": bool(authed and user.is_at_least(Level.CREATOR)),
    }
