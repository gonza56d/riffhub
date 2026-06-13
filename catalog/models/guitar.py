from django.db import models

from catalog.constants import (
    NECK_THICK_MIN_MM,
    NECK_THIN_MAX_MM,
    PICKUP_POSITION_ORDER,
    ElectronicsType,
    NeckThickness,
    PickupPosition,
)

from .base import CatalogEntry


class GuitarModel(CatalogEntry):
    """A guitar model: the central, filterable entity.

    Hand-entered specs are real columns / FK lookups. The "facets" that the
    spec asked to be *calculated* from components live in the ``DERIVED`` block
    below — denormalised into indexed columns so they're fast to filter, and
    recomputed from the attached gear via signals (see ``catalog.signals``).
    """

    brand = models.ForeignKey(
        "catalog.Brand", on_delete=models.PROTECT, related_name="guitars"
    )
    name = models.CharField(max_length=150)
    year_introduced = models.PositiveIntegerField(null=True, blank=True)
    year_discontinued = models.PositiveIntegerField(null=True, blank=True)

    # --- Strings & scale (scale is composable: min != max => multiscale) ---
    num_strings = models.PositiveSmallIntegerField(db_index=True)
    scale_length_min_inches = models.DecimalField(
        max_digits=5, decimal_places=3, db_index=True
    )
    scale_length_max_inches = models.DecimalField(
        max_digits=5, decimal_places=3, db_index=True
    )

    # --- Frets & fretboard -------------------------------------------------
    num_frets = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    fret_material = models.ForeignKey(
        "catalog.FretMaterial", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars",
    )
    is_fretless = models.BooleanField(default=False, db_index=True)
    fretboard_material = models.ForeignKey(
        "catalog.FretboardMaterial", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars",
    )
    fretboard_radius = models.ForeignKey(
        "catalog.FretboardRadius", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars",
    )

    # --- Neck --------------------------------------------------------------
    neck_construction = models.ForeignKey(
        "catalog.NeckConstruction", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars", db_index=True,
    )
    neck_material = models.ForeignKey(
        "catalog.NeckMaterial", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars",
    )
    neck_profile = models.ForeignKey(
        "catalog.NeckProfile", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars",
    )
    neck_depth_1st_fret_mm = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True
    )
    neck_depth_12th_fret_mm = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True
    )
    nut_width_mm = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True, db_index=True
    )

    # --- Body & hardware ---------------------------------------------------
    body_material = models.ForeignKey(
        "catalog.BodyMaterial", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars",
    )
    body_shape = models.ForeignKey(
        "catalog.BodyShape", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars",
    )
    headstock_type = models.ForeignKey(
        "catalog.HeadstockType", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars",
    )
    selector_switch = models.ForeignKey(
        "catalog.SelectorSwitch", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars",
    )
    country_of_origin = models.ForeignKey(
        "catalog.Country", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars", db_index=True,
    )

    # --- Components (gear) -------------------------------------------------
    bridge = models.ForeignKey(
        "catalog.Bridge", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars",
    )
    nut = models.ForeignKey(
        "catalog.Nut", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars",
    )
    tuners = models.ForeignKey(
        "catalog.Tuner", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="guitars",
    )
    pickups = models.ManyToManyField(
        "catalog.Pickup", through="catalog.GuitarPickup",
        related_name="guitars", blank=True,
    )

    # --- DERIVED facets (denormalised; recomputed from components) ---------
    pickup_combination = models.CharField(max_length=12, blank=True, db_index=True)
    electronics_type = models.CharField(
        max_length=10, choices=ElectronicsType.choices,
        default=ElectronicsType.UNKNOWN, db_index=True,
    )
    has_hum_cancellation = models.BooleanField(default=False, db_index=True)
    has_tremolo = models.BooleanField(default=False, db_index=True)
    has_piezo = models.BooleanField(default=False, db_index=True)
    has_locking_tuners = models.BooleanField(default=False, db_index=True)
    neck_thickness_class = models.CharField(
        max_length=10, choices=NeckThickness.choices,
        default=NeckThickness.UNKNOWN, db_index=True,
    )
    is_multiscale = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["brand__name", "name"]

    def __str__(self) -> str:
        return f"{self.brand} {self.name}"

    # --- Derived spec computation -----------------------------------------
    def _classify_neck_thickness(self) -> str:
        depth = self.neck_depth_1st_fret_mm
        if depth is None:
            return NeckThickness.UNKNOWN
        depth = float(depth)
        if depth <= NECK_THIN_MAX_MM:
            return NeckThickness.THIN
        if depth >= NECK_THICK_MIN_MM:
            return NeckThickness.THICK
        return NeckThickness.MEDIUM

    def compute_derived(self) -> dict:
        """Compute (without saving) the denormalised facet values from this
        guitar's own fields and its attached components."""
        values = {
            "is_multiscale": (
                self.scale_length_min_inches != self.scale_length_max_inches
            ),
            "neck_thickness_class": self._classify_neck_thickness(),
        }

        bridge = self.bridge
        bridge_type = bridge.bridge_type if bridge else None
        values["has_tremolo"] = bool(bridge_type and bridge_type.is_tremolo)
        values["has_piezo"] = bool(bridge and bridge.has_piezo)

        values["has_locking_tuners"] = bool(self.tuners and self.tuners.is_locking)

        if self.pk:
            links = list(
                self.guitar_pickups.select_related("pickup__pickup_type").all()
            )
            links.sort(key=lambda link: PICKUP_POSITION_ORDER.get(link.position, 99))
            types = [
                link.pickup.pickup_type
                for link in links
                if link.pickup and link.pickup.pickup_type_id
            ]
            values["pickup_combination"] = "".join(t.symbol for t in types)
            values["has_hum_cancellation"] = any(t.is_humbucking for t in types)

            actives = [link.pickup.is_active for link in links if link.pickup]
            if not actives:
                values["electronics_type"] = ElectronicsType.UNKNOWN
            elif all(actives):
                values["electronics_type"] = ElectronicsType.ACTIVE
            elif not any(actives):
                values["electronics_type"] = ElectronicsType.PASSIVE
            else:
                values["electronics_type"] = ElectronicsType.MIXED
        else:
            values["pickup_combination"] = ""
            values["has_hum_cancellation"] = False
            values["electronics_type"] = ElectronicsType.UNKNOWN

        return values

    def recompute_derived(self) -> None:
        """Persist derived facets via a direct UPDATE (bypasses save/signals,
        so it never recurses)."""
        if not self.pk:
            return
        values = self.compute_derived()
        type(self).objects.filter(pk=self.pk).update(**values)
        for key, value in values.items():
            setattr(self, key, value)


class GuitarPickup(models.Model):
    """Through model linking a guitar to a pickup in a specific position."""

    guitar = models.ForeignKey(
        "catalog.GuitarModel", on_delete=models.CASCADE, related_name="guitar_pickups"
    )
    pickup = models.ForeignKey(
        "catalog.Pickup", on_delete=models.PROTECT, related_name="guitar_links"
    )
    position = models.CharField(max_length=10, choices=PickupPosition.choices)

    class Meta:
        unique_together = [("guitar", "position")]
        ordering = ["guitar", "position"]

    def __str__(self) -> str:
        return f"{self.guitar} – {self.get_position_display()}: {self.pickup}"
