"""Seed demo forum content — posts, comments, votes and reactions — plus the
fake users that author them, into every **empty** subtopic.

Fake users are named "{Name} Test" (e.g. "John Test") with a deliberate mix of
levels — Regular, Collaborator-eligible (``accepted_submissions_count`` set, so
they derive as Database Collaborator once the promotion threshold is configured)
and sticky Founders (``is_founder``). It NEVER creates a Community Moderator or
Riffhub Creator — those granted roles stay manual.

Idempotent and safe to re-run (including on a live deploy):
  * fake users are ``get_or_create``d by username, never duplicated;
  * content is only added to subtopics that currently have NO posts, so a
    re-run simply fills any newly-empty subtopics and skips populated ones.

Run after ``seed_forum`` (which creates the predefined topics/subtopics)::

    manage.py seed_forum_content
"""

import random
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from forum import services
from forum.constants import DEFAULT_CURRENCY, VoteValue
from forum.models import Subtopic

User = get_user_model()


# (first name, level-shaping attrs). NO moderators or creators by design.
FAKE_USERS = [
    ("John", {"reputation_score": 4}),                                          # Regular
    ("Alex", {"reputation_score": 1}),                                          # Regular
    ("Pat", {"reputation_score": 6}),                                           # Regular
    ("Morgan", {"reputation_score": 2}),                                        # Regular
    ("Riley", {"reputation_score": 3}),                                         # Regular
    ("Jane", {"reputation_score": 12, "accepted_submissions_count": 6}),        # Collaborator-eligible
    ("Chris", {"reputation_score": 8, "accepted_submissions_count": 4}),        # Collaborator-eligible
    ("Jordan", {"reputation_score": 17, "accepted_submissions_count": 8}),      # Collaborator-eligible
    ("Casey", {"reputation_score": 9, "accepted_submissions_count": 5}),        # Collaborator-eligible
    ("Dana", {"reputation_score": 14, "accepted_submissions_count": 7}),        # Collaborator-eligible
    ("Sam", {"reputation_score": 28, "accepted_submissions_count": 9, "is_founder": True}),   # Founder
    ("Taylor", {"reputation_score": 22, "is_founder": True}),                   # Founder
]

REACTIONS = ["🔥", "🤘", "🎸", "👍", "❤️", "🙌"]

POST_TEMPLATES = [
    ("Anyone else deep into {name}?", "Been spending all my time on {name} lately. What's everyone playing or listening to right now?"),
    ("My current {name} setup", "Sharing where my {name} rig landed this year. Always tweaking — feedback welcome."),
    ("{name}: the underrated picks", "A few {name} gems I think deserve way more attention. Drop yours below."),
    ("Getting started with {name}", "New to the {name} side of things. Where would you tell a beginner to start?"),
]

MARKET_TEMPLATES = [
    ("FS: {name} — excellent condition", "Selling a lovely piece from my {name} stash. More pics on request, no trades please."),
    ("WTB: something in {name}", "Looking to buy in the {name} category. Reasonable budget — show me what you've got."),
    ("Price drop — {name}", "Reduced my {name} listing, needs to go this month. Serious offers only."),
]

COMMENTS = [
    "Love this — thanks for sharing.",
    "Solid pick. I'd throw a couple of my own favourites in too.",
    "How does that hold up over a long session? Tempted.",
    "Been on the fence about this for ages, this helps.",
    "Great thread, following along.",
    "Respectfully disagree, but I get where you're coming from.",
    "This is exactly what I needed to read today.",
]

MARKET_PRICES = ["250.00", "450.00", "800.00", "1200.00", "1850.00", "2500.00"]


class Command(BaseCommand):
    help = (
        "Seed demo posts/comments/votes/reactions (and fake users) into every "
        "EMPTY subtopic. Idempotent — only fills subtopics with no posts."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(1989)  # fixed seed -> reproducible content
        users, users_created = self._ensure_users()

        filled = skipped = 0
        n_posts = n_comments = n_votes = n_reactions = 0

        for subtopic in Subtopic.objects.select_related("topic").all():
            if subtopic.posts.exists():
                skipped += 1
                continue
            filled += 1
            is_market = subtopic.topic.is_market
            templates = MARKET_TEMPLATES if is_market else POST_TEMPLATES
            chosen = rng.sample(templates, rng.randint(2, len(templates)))

            for title_t, body_t in chosen:
                author = rng.choice(users)
                extra = {}
                if is_market:
                    # A real seller would have accepted the disclaimer first.
                    services.accept_market_disclaimer(author)
                    extra["price"] = rng.choice(MARKET_PRICES)
                    extra["currency"] = DEFAULT_CURRENCY
                post = services.create_post(
                    subtopic=subtopic,
                    author=author,
                    title=title_t.format(name=subtopic.name),
                    body=body_t.format(name=subtopic.name),
                    **extra,
                )
                n_posts += 1

                for _ in range(rng.randint(1, 3)):
                    services.create_comment(
                        post=post, author=rng.choice(users), body=rng.choice(COMMENTS)
                    )
                    n_comments += 1

                # Votes & reactions come from users OTHER than the author
                # (self-votes/reactions are rejected by the services).
                others = [u for u in users if u.pk != author.pk]
                rng.shuffle(others)
                for voter in others[: rng.randint(2, 5)]:
                    value = VoteValue.UP if rng.random() < 0.8 else VoteValue.DOWN
                    services.cast_vote(voter, post, value)
                    n_votes += 1
                rng.shuffle(others)
                for reactor in others[: rng.randint(1, 3)]:
                    services.toggle_reaction(reactor, post, rng.choice(REACTIONS))
                    n_reactions += 1

        self.stdout.write(self.style.SUCCESS(
            f"Fake users: {users_created} created, {len(users)} total. "
            f"Subtopics: {filled} filled, {skipped} already had posts. "
            f"Created {n_posts} posts, {n_comments} comments, "
            f"{n_votes} votes, {n_reactions} reactions."
        ))

    def _ensure_users(self):
        """get_or_create the fake "{Name} Test" user pool (idempotent)."""
        users = []
        created = 0
        for name, attrs in FAKE_USERS:
            user, was_created = User.objects.get_or_create(
                username=f"{name} Test",
                defaults={
                    "email": f"{name.lower()}.test@example.com",
                    "email_confirmed": True,
                    **attrs,
                },
            )
            created += int(was_created)
            users.append(user)
        return users, created
