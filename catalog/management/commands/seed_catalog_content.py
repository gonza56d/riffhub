"""Seed demo comments + one-level replies on catalog detail pages.

The catalog counterpart to ``seed_forum_content``: gives a deterministic sample
of published guitars (instruments) and gear pieces (Bridge / Pickup / Tuner /
Nut) a short comment thread with a few replies, authored by the shared
"{Name} Test" pool (``accounts.seed``).

Idempotent: a stable every-Nth slice (by pk) of each type is targeted, and any
item that already has a comment is skipped — so re-running adds nothing and
simply covers any newly-eligible items. Run after the catalog is seeded
(``seed_catalog`` / ``seed_catalog_csv``)::

    manage.py seed_catalog_content
"""

import random

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.seed import ensure_fake_users
from catalog import services
from catalog.models import Bridge, CatalogComment, GuitarModel, Nut, Pickup, Tuner

COMMENTS = [
    "Owned one of these — fantastic.",
    "How's the sustain on this?",
    "Criminally underrated piece of kit.",
    "Build quality is incredible for the price.",
    "Been eyeing this for months.",
    "Mine's held up for years, zero issues.",
    "Tone is exactly what I was after.",
    "Great spec sheet — thanks for cataloguing it.",
]

REPLIES = [
    "Agreed, can't fault it.",
    "Same here — rock solid.",
    "Good question, I'd like to know too.",
    "Depends on the setup, but mostly yes.",
    "Thanks for the tip!",
    "Couldn't have said it better.",
]

# Every Nth published item (by pk) of a kind gets a thread. Guitars are the
# headline pages, so a denser slice than the many gear pieces.
GUITAR_EVERY = 4
GEAR_EVERY = 9
REPLY_CHANCE = 0.6  # chance that a seeded comment also gets replies


class Command(BaseCommand):
    help = (
        "Seed demo comments + replies onto a deterministic sample of published "
        "guitars and gear. Idempotent — skips items that already have comments."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        users, users_created = ensure_fake_users()
        totals = {"items": 0, "comments": 0, "replies": 0}

        self._seed_kind(GuitarModel, GUITAR_EVERY, users, totals)
        for model in (Bridge, Pickup, Tuner, Nut):
            self._seed_kind(model, GEAR_EVERY, users, totals)

        self.stdout.write(self.style.SUCCESS(
            f"Fake users: {users_created} created. "
            f"Seeded {totals['comments']} comments and {totals['replies']} "
            f"replies across {totals['items']} catalog items."
        ))

    def _seed_kind(self, model, every, users, totals) -> None:
        ct = ContentType.objects.get_for_model(model, for_concrete_model=False)
        published = model.objects.published().select_related("brand").order_by("pk")
        for index, item in enumerate(published):
            if index % every != 0:
                continue
            if CatalogComment.objects.filter(
                content_type=ct, object_id=item.pk
            ).exists():
                continue  # already has a thread -> idempotent skip
            irng = random.Random(f"{ct.id}:{item.pk}")  # stable per item
            totals["items"] += 1
            for _ in range(irng.randint(1, 3)):
                top = services.add_catalog_comment(
                    target=item, author=irng.choice(users), body=irng.choice(COMMENTS)
                )
                totals["comments"] += 1
                if irng.random() < REPLY_CHANCE:
                    for _ in range(irng.randint(1, 2)):
                        services.add_catalog_comment(
                            target=item,
                            author=irng.choice(users),
                            body=irng.choice(REPLIES),
                            parent=top,
                        )
                        totals["replies"] += 1
