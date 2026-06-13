"""Seed the predefined forum topics and subtopics (idempotent).

Mirrors the "Pre-defined/initial forum topics and subtopics" table in
PRODUCT.md, plus the special "Gear Market" selling section. Safe to run
repeatedly: existing rows are looked up by their natural keys (topic name /
subtopic name within topic) and updated in place, never duplicated.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from forum.constants import (
    GEAR_MARKET_SUBTOPICS,
    GEAR_MARKET_TOPIC_NAME,
    PREDEFINED_TOPICS,
)
from forum.models import Subtopic, Topic


class Command(BaseCommand):
    help = "Create the predefined forum topics and subtopics (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        topics_created = subtopics_created = 0

        # --- Regular predefined topics (not market) -----------------------
        for topic_name, subtopic_names in PREDEFINED_TOPICS:
            topic, created = Topic.objects.get_or_create(
                name=topic_name,
                defaults={"is_predefined": True},
            )
            topics_created += int(created)
            # Keep the predefined flag correct even on pre-existing rows.
            if not topic.is_predefined:
                topic.is_predefined = True
                topic.save(update_fields=["is_predefined"])
            subtopics_created += self._seed_subtopics(topic, subtopic_names)

        # --- Gear Market: selling section, requires disclaimer ------------
        market, created = Topic.objects.get_or_create(
            name=GEAR_MARKET_TOPIC_NAME,
            defaults={
                "is_market": True,
                "requires_disclaimer": True,
                "is_predefined": True,
            },
        )
        topics_created += int(created)
        # Ensure the special flags are set even if the row predated this seed.
        updates = {}
        if not market.is_market:
            updates["is_market"] = market.is_market = True
        if not market.requires_disclaimer:
            updates["requires_disclaimer"] = market.requires_disclaimer = True
        if not market.is_predefined:
            updates["is_predefined"] = market.is_predefined = True
        if updates:
            market.save(update_fields=list(updates))
        subtopics_created += self._seed_subtopics(market, GEAR_MARKET_SUBTOPICS)

        self.stdout.write(
            self.style.SUCCESS(
                f"Forum seed complete: {topics_created} topic(s) and "
                f"{subtopics_created} subtopic(s) created."
            )
        )

    def _seed_subtopics(self, topic: Topic, names) -> int:
        created_count = 0
        for name in names:
            _, created = Subtopic.objects.get_or_create(topic=topic, name=name)
            created_count += int(created)
        return created_count
