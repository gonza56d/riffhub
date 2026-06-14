from django.db import models


class VoteValue(models.IntegerChoices):
    """Up/down vote values.

    Stored as ``+1`` / ``-1`` so a tally can sum them, while PRODUCT.md also
    asks us to count positives and negatives *separately* (see
    ``forum.services.vote_tally``).
    """

    UP = 1, "Up"
    DOWN = -1, "Down"


class ProposalStatus(models.TextChoices):
    """Lifecycle of a community topic/subtopic proposal."""

    OPEN = "open", "Open"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"


class ProposalVoteValue(models.IntegerChoices):
    """Up/down vote on a community proposal (separate from content votes)."""

    UP = 1, "For"
    DOWN = -1, "Against"


# --- Predefined Gear Market currency default -------------------------------
# Posts in the Gear Market carry a free-form ISO-ish currency code alongside
# their price. Kept short; not a controlled vocabulary (sellers worldwide).
DEFAULT_CURRENCY = "USD"
CURRENCY_MAX_LENGTH = 8


# --- Image attachment policy (filesystem + Pillow, per PRODUCT.md) ---------
# These are the sane limits the spec asked us to "pick and document". Uploads
# are validated with Pillow in ``Attachment.clean`` / the field validator.
ATTACHMENT_UPLOAD_DIR = "forum/"
# Allowed Pillow formats (verified via ``Image.format`` after ``verify()``).
ALLOWED_IMAGE_FORMATS = ("JPEG", "PNG", "GIF", "WEBP")
# Max stored file size: 5 MiB. Multi-MB binaries stay on disk, not in Postgres.
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
# Max pixel dimensions (width/height). Guards against decompression bombs and
# absurdly large uploads while comfortably covering high-DPI gear photos.
MAX_IMAGE_WIDTH = 4096
MAX_IMAGE_HEIGHT = 4096


# --- Activity ---------------------------------------------------------------
# Per PRODUCT.md ANY user action (post/comment/vote/react) bumps activity by 1
# on the subtopic and its parent topic. Centralised so the weight is obvious.
ACTIVITY_INCREMENT = 1


# --- Reputation weights -----------------------------------------------------
# PRODUCT.md: a user's score marks how participative/collaborative they are,
# from posting/commenting AND (separately) accepted collab-db uploads. These
# are starting weights — candidates to move into SiteConfiguration if the
# community wants to tune them. (Accepted-submission reputation lives in
# catalog.constants.REP_ACCEPTED_SUBMISSION.)
REP_POST_CREATED = 2
REP_COMMENT_CREATED = 1
REP_RECEIVED_UPVOTE = 1
REP_RECEIVED_DOWNVOTE = -1


# --- Predefined forum hierarchy (seed_forum) -------------------------------
# (topic_name, [subtopic names]) for the four predefined, non-market topics.
PREDEFINED_TOPICS = (
    ("Gear", ("Guitars", "Basses", "Percussion", "Studio", "Other")),
    ("State Of Art", ("Metal", "Blues", "Classic Rock", "Other")),
    ("Events", ("Metal", "Blues", "Classic Rock", "Other")),
)

# The Gear Market is special: selling is allowed here, a disclaimer is required
# before participating, and posts unlock the price field.
GEAR_MARKET_TOPIC_NAME = "Gear Market"
GEAR_MARKET_SUBTOPICS = ("Guitars", "Basses", "Studio", "Percussion", "Other")

# Shown in place of a comment/reply that its author has deleted (soft delete).
# Moderators and Riffhub Creators can still reveal the original (see
# ``forum.views.comment_original``); everyone else only ever sees this text.
DELETED_COMMENT_PLACEHOLDER = "This message was deleted."


# Wording the user must accept before participating in the Gear Market.
MARKET_DISCLAIMER_TEXT = (
    "riffhub is not responsible for any sale, purchase, coordination of "
    "meetings, or payments arranged between users in the Gear Market. You "
    "participate at your own risk."
)
