from django.db import models


class PublicationStatus(models.TextChoices):
    UNDER_REVISION = "under_revision", "Under revision"
    PUBLISHED = "published", "Published"
    REJECTED = "rejected", "Rejected"


class VoteValue(models.IntegerChoices):
    """The two values a collab-db review vote may take."""

    UP = 1, "Upvote"
    DOWN = -1, "Downvote"


# Reputation awarded to a submitter when one of their catalog contributions is
# accepted (PRODUCT.md: the score rewards uploading new gear, not just forum
# participation). Forum participation weights live in forum.constants.
REP_ACCEPTED_SUBMISSION = 10


class CorrectionStatus(models.TextChoices):
    """Lifecycle of a proposed correction to a catalog entry."""

    OPEN = "open", "Open"
    APPLIED = "applied", "Applied"
    REJECTED = "rejected", "Rejected"


class PickupPosition(models.TextChoices):
    BRIDGE = "bridge", "Bridge"
    MIDDLE = "middle", "Middle"
    NECK = "neck", "Neck"


# Combination strings are read bridge -> neck: an "HSS" guitar carries the
# humbucker at the bridge. Lower number sorts first.
PICKUP_POSITION_ORDER = {
    PickupPosition.BRIDGE: 0,
    PickupPosition.MIDDLE: 1,
    PickupPosition.NECK: 2,
}


class ElectronicsType(models.TextChoices):
    PASSIVE = "passive", "Passive"
    ACTIVE = "active", "Active"
    MIXED = "mixed", "Mixed"
    UNKNOWN = "unknown", "Unknown"


class NeckThickness(models.TextChoices):
    THIN = "thin", "Thin"
    MEDIUM = "medium", "Medium"
    THICK = "thick", "Thick"
    UNKNOWN = "unknown", "Unknown"


class TunerType(models.TextChoices):
    SEALED = "sealed", "Sealed"
    OPEN_GEAR = "open_gear", "Open-gear"
    VINTAGE = "vintage", "Vintage"
    LOCKING = "locking", "Locking"


# Neck-thickness classification thresholds (mm, measured at the 1st fret).
# Tunable; could move to SiteConfiguration later if the community wants it.
NECK_THIN_MAX_MM = 19.5
NECK_THICK_MIN_MM = 21.5
