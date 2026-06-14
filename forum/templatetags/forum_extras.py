"""Template helpers for the forum.

``comment_body`` renders a comment's text safely (everything HTML-escaped) while
turning ``@username`` tags of the *non-banned* users that were actually tagged
on it into profile links. Only handles stored in ``comment.mentions`` (resolved
to non-banned accounts at creation, see ``forum.services._apply_mentions``) are
linked; any other ``@handle`` stays plain escaped text, so a banned or unknown
user is never turned into a live link.
"""

import re

from django import template
from django.urls import reverse
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe

register = template.Library()

# Mirror the parser in ``forum.services`` so render and storage agree on what a
# handle looks like.
_MENTION_RE = re.compile(r"@([\w.@+-]+)")


@register.filter
def comment_body(comment):
    """Render ``comment.body`` with escaped text, linked mentions and <br>s.

    Expects ``comment.mentions`` to be prefetched on the queryset to avoid a
    query per comment.
    """
    text = comment.body or ""
    linkable = {u.username: u for u in comment.mentions.all()}
    parts = []
    last = 0
    for match in _MENTION_RE.finditer(text):
        parts.append(conditional_escape(text[last:match.start()]))
        user = linkable.get(match.group(1))
        if user is not None:
            parts.append(
                format_html(
                    '<a class="mention" href="{}">@{}</a>',
                    reverse("profile", args=[user.username]),
                    user.username,
                )
            )
        else:
            parts.append(conditional_escape(match.group(0)))
        last = match.end()
    parts.append(conditional_escape(text[last:]))
    return mark_safe("".join(str(part) for part in parts).replace("\n", "<br>"))
