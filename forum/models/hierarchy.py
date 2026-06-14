from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from core.models import Moderatable, TimeStampedModel

from forum.constants import CURRENCY_MAX_LENGTH


class Topic(models.Model):
    """Top of the forum hierarchy (e.g. "Gear", "Gear Market").

    Predefined topics are seeded by ``seed_forum``; the community can propose
    new ones (see ``forum.models.proposals``). Sorted by activity so the
    liveliest topics surface first (PRODUCT.md).
    """

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)

    # The Gear Market is a market topic: selling is allowed, posts unlock the
    # price field, and a disclaimer must be accepted before participating.
    is_market = models.BooleanField(default=False)
    requires_disclaimer = models.BooleanField(default=False)
    # Predefined topics are seeded and protected from community deletion.
    is_predefined = models.BooleanField(default=False)

    # Denormalised activity counter (any action bumps it); indexed for ordering.
    activity_count = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        ordering = ["-activity_count", "name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:120]
        super().save(*args, **kwargs)


class Subtopic(models.Model):
    """Second level of the hierarchy, scoped to a :class:`Topic`.

    Slugs are unique *within* a topic (the same "Guitars" can live under both
    "Gear" and "Gear Market"). Sorted by activity (PRODUCT.md).
    """

    topic = models.ForeignKey(
        "forum.Topic", on_delete=models.CASCADE, related_name="subtopics"
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, blank=True)
    activity_count = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        ordering = ["-activity_count", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "slug"], name="unique_subtopic_slug_per_topic"
            )
        ]

    def __str__(self) -> str:
        return f"{self.topic} / {self.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:120]
        super().save(*args, **kwargs)


class Post(TimeStampedModel, Moderatable):
    """A thread inside a subtopic: has a title and a body.

    In the Gear Market a post is a listing, so ``price``/``currency`` are
    required there and forbidden everywhere else (enforced in ``clean``).
    Videos are external links only (``video_url``); images are attached via
    ``forum.models.Attachment``.
    """

    subtopic = models.ForeignKey(
        "forum.Subtopic", on_delete=models.CASCADE, related_name="posts"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="forum_posts",
    )
    title = models.CharField(max_length=255)
    body = models.TextField()

    # External video link only (YouTube etc.) — never an uploaded file.
    video_url = models.URLField(
        blank=True, help_text="External video link only (e.g. YouTube)."
    )

    # Gear Market only: the asking price of the listing.
    price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=CURRENCY_MAX_LENGTH, blank=True)

    activity_count = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        """Enforce the market/price coupling (PRODUCT.md "Selling stuff").

        A price only makes sense for a Gear Market listing: require it there,
        forbid it everywhere else so non-market posts can never carry a price.
        """
        super().clean()
        is_market = self.subtopic.topic.is_market
        if is_market:
            if self.price is None:
                raise ValidationError(
                    {"price": "A price is required for Gear Market listings."}
                )
            if self.price < 0:
                raise ValidationError(
                    {"price": "A price cannot be negative."}
                )
        elif self.price is not None:
            raise ValidationError(
                {"price": "Only Gear Market posts may set a price."}
            )


class Comment(TimeStampedModel, Moderatable):
    """A reply on a :class:`Post`. Body only — no title, no price.

    Lowball offers in the Gear Market are just regular comments (PRODUCT.md).
    An optional external ``video_url`` mirrors posts for parity.
    """

    post = models.ForeignKey(
        "forum.Post", on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="forum_comments",
    )
    body = models.TextField()
    video_url = models.URLField(
        blank=True, help_text="External video link only (e.g. YouTube)."
    )

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Comment by {self.author} on {self.post}"
