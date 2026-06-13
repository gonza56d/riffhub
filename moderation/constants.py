from datetime import timedelta

from django.db import models


class ContentActionType(models.TextChoices):
    MOVE = "move", "Moved"
    REMOVE = "remove", "Removed"
    RESTORE = "restore", "Restored"


# Escalating silence durations (PRODUCT.md): 1st = one week, 2nd = one month,
# 3rd-and-beyond = permanent (and publicly flagged). Tunable here.
SILENCE_DURATIONS = {1: timedelta(weeks=1), 2: timedelta(days=30)}
