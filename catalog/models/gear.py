from django.db import models

from catalog.constants import TunerType

from .base import CatalogEntry


class GearItem(CatalogEntry):
    """Abstract base for the four catalog gear types riffhub tracks.

    These are exactly the components that *drive* guitar specs (bridge ->
    tremolo/piezo, pickups -> combination/active/hum, tuners -> locking, nut ->
    material). Easy-swap hardware (knobs, straps, ...) is intentionally out.
    """

    brand = models.ForeignKey(
        "catalog.Brand", on_delete=models.PROTECT, related_name="%(class)ss"
    )
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="gear/", blank=True, null=True)

    class Meta:
        abstract = True
        ordering = ["brand__name", "name"]

    def __str__(self) -> str:
        return f"{self.brand} {self.name}"


class Bridge(GearItem):
    bridge_type = models.ForeignKey(
        "catalog.BridgeType", on_delete=models.PROTECT, related_name="bridges"
    )
    has_piezo = models.BooleanField(default=False)
    is_locking = models.BooleanField(default=False)


class Pickup(GearItem):
    pickup_type = models.ForeignKey(
        "catalog.PickupType", on_delete=models.PROTECT, related_name="pickups"
    )
    is_active = models.BooleanField(
        default=False, help_text="Active (battery-powered) vs passive."
    )


class Tuner(GearItem):
    is_locking = models.BooleanField(default=False)
    ratio = models.CharField(max_length=10, blank=True, help_text='e.g. "18:1".')
    tuner_type = models.CharField(
        max_length=20, choices=TunerType.choices, blank=True
    )


class Nut(GearItem):
    material = models.ForeignKey(
        "catalog.NutMaterial", on_delete=models.PROTECT, related_name="nuts"
    )
    is_locking = models.BooleanField(
        default=False, help_text="Locking nut (e.g. for a Floyd Rose system)."
    )
