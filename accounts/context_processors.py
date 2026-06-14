"""Template context for UI preferences.

Exposes the viewer's active colour theme to every template, so the base layout
can render it on ``<html data-theme="...">`` (avoiding a flash of the wrong
theme) without each view passing it explicitly. A logged-in user's saved
preference wins; otherwise the theme cookie is used; the default is light — the
original look.
"""

from accounts.models import THEME_COOKIE_NAME, Theme

_VALID = set(Theme.values)


def theme(request) -> dict:
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        active = user.theme
    else:
        active = request.COOKIES.get(THEME_COOKIE_NAME, Theme.LIGHT.value)
    if active not in _VALID:
        active = Theme.LIGHT.value
    return {"active_theme": active}


def email_confirmation(request) -> dict:
    """Whether to prompt the viewer to confirm their e-mail.

    Respects the ``REQUIRE_EMAIL_CONFIRMATION`` toggle: when confirmation is
    disabled, ``has_confirmed_email`` is always true, so the prompt is hidden.
    """
    from accounts.services import has_confirmed_email

    user = getattr(request, "user", None)
    needs = bool(
        user is not None and user.is_authenticated and not has_confirmed_email(user)
    )
    return {"needs_email_confirmation": needs}
