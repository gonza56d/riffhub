from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from core.models import Moderatable, TimeStampedModel


class CatalogComment(TimeStampedModel, Moderatable):
    """A user comment (or one-level reply) on a catalog entity.

    Attaches generically to any catalog object with a detail page — a
    ``GuitarModel`` or one of the four gear types (Bridge/Pickup/Tuner/Nut) — so
    one thread mechanism serves every page. This is deliberately *separate* from
    the forum ``Comment`` (which is Post-bound): the catalog domain owns its own
    thread model.

    Replies are a single level deep — a reply points at a top-level comment via
    ``parent``; replying to a reply is rejected in the service layer. Inherits
    ``is_removed`` from ``Moderatable`` so removed comments can be hidden and
    future moderation composes (no deletion UI is built here).
    """

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="catalog_comments",
    )
    body = models.TextField()
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="replies",
    )

    class Meta:
        # Chronological by default — right for replies under a comment. The
        # top-level listing re-orders newest-first explicitly (see services).
        ordering = ["created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self) -> str:
        kind = "reply" if self.parent_id else "comment"
        return f"{self.author} {kind} on {self.target}"
