from accounts.models import Level


def moderation_flags(request):
    """Expose ``is_moderator`` to every template so nav + inline moderator
    controls can show conditionally without each view passing it."""
    user = getattr(request, "user", None)
    is_moderator = bool(
        user and user.is_authenticated and user.is_at_least(Level.MODERATOR)
    )
    return {"is_moderator": is_moderator}
