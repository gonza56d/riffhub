from django.db import models

from .base import CatalogEntry


class Brand(CatalogEntry):
    name = models.CharField(max_length=120, unique=True)
    country = models.ForeignKey(
        "catalog.Country",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="brands",
    )
    website = models.URLField(blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
