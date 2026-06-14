from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from catalog.constants import PublicationStatus
from core.models import TimeStampedModel


class CatalogQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=PublicationStatus.PUBLISHED)

    def under_revision(self):
        return self.filter(status=PublicationStatus.UNDER_REVISION)


class CatalogEntry(TimeStampedModel):
    """Abstract base for every collab-db entity (brands, gear, guitars).

    New community contributions land as ``UNDER_REVISION`` and are hidden from
    normal catalog queries until accepted (``PUBLISHED``). Seed/admin data can
    be created already published.
    """

    status = models.CharField(
        max_length=20,
        choices=PublicationStatus.choices,
        default=PublicationStatus.UNDER_REVISION,
        db_index=True,
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="%(class)s_submissions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    objects = CatalogQuerySet.as_manager()

    class Meta:
        abstract = True

    def mark_published(self, *, save: bool = True) -> None:
        now = timezone.now()
        self.status = PublicationStatus.PUBLISHED
        self.reviewed_at = now
        self.published_at = now
        if save:
            self.save(
                update_fields=["status", "reviewed_at", "published_at", "updated_at"]
            )


class ControlledVocabulary(TimeStampedModel):
    """Abstract base for the small, collaboratively-extensible lookup tables
    that back categorical guitar specs (materials, shapes, profiles, ...)."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)
    is_approved = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:120]
        super().save(*args, **kwargs)
