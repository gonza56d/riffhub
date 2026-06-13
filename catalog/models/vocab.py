from django.db import models

from .base import ControlledVocabulary


# --- Plain categorical vocabularies ----------------------------------------
class FretMaterial(ControlledVocabulary):
    pass


class FretboardMaterial(ControlledVocabulary):
    pass


class NeckConstruction(ControlledVocabulary):
    pass


class NeckMaterial(ControlledVocabulary):
    pass


class NeckProfile(ControlledVocabulary):
    pass


class BodyMaterial(ControlledVocabulary):
    pass


class BodyShape(ControlledVocabulary):
    pass


class HeadstockType(ControlledVocabulary):
    pass


class SelectorSwitch(ControlledVocabulary):
    pass


class NutMaterial(ControlledVocabulary):
    pass


class Country(ControlledVocabulary):
    class Meta(ControlledVocabulary.Meta):
        verbose_name_plural = "Countries"


# --- Vocabularies carrying extra structure ---------------------------------
class FretboardRadius(ControlledVocabulary):
    """Radius supports compound ("composed") values: min/max in inches.

    Single radius => min == max. Perfectly flat boards use ``is_flat``.
    """

    radius_min_inches = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    radius_max_inches = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    is_compound = models.BooleanField(default=False)
    is_flat = models.BooleanField(default=False)

    class Meta(ControlledVocabulary.Meta):
        verbose_name_plural = "Fretboard radii"


class PickupType(ControlledVocabulary):
    """e.g. Humbucker (symbol "H", hum-cancelling) or Single-coil ("S")."""

    symbol = models.CharField(
        max_length=2,
        help_text='Letter used in combination strings, e.g. "H" or "S".',
    )
    is_humbucking = models.BooleanField(
        default=False, help_text="Counts toward a guitar's hum-cancellation facet."
    )


class BridgeType(ControlledVocabulary):
    """e.g. Hardtail, Tune-o-matic, Vintage tremolo, Locking tremolo (Floyd)."""

    is_tremolo = models.BooleanField(default=False)
    is_locking = models.BooleanField(default=False)
