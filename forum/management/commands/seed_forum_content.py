"""Seed demo forum content — posts, comments, votes, reactions and replies.

Two idempotent passes, both safe to re-run (including on a live deploy):
  * fill every **empty** subtopic with posts (priced listings in the Gear
    Market), each carrying comments, up/down votes and emoji reactions;
  * add a reply or two onto a deterministic ~half of the existing top-level
    comments that don't have any replies yet.

Fake authors come from the shared "{Name} Test" pool (``accounts.seed``) — a mix
of Regular / Collaborator-eligible / Founder, never a Moderator or Creator.

Run after ``seed_forum`` (which creates the predefined topics/subtopics)::

    manage.py seed_forum_content
"""

import random

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.seed import ensure_fake_users
from forum import services
from forum.constants import DEFAULT_CURRENCY, VoteValue
from forum.models import Comment, Subtopic

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

REPLIES = [
    "Totally agree with this.",
    "Good point — hadn't thought of it that way.",
    "Same experience here.",
    "Have you tried the other option though?",
    "This, 100%.",
    "Thanks, that's genuinely helpful.",
]

MARKET_PRICES = ["250.00", "450.00", "800.00", "1200.00", "1850.00", "2500.00"]

# A stable ~half of reply-less top-level comments get replies.
REPLY_PROBABILITY = 0.5


class Command(BaseCommand):
    help = (
        "Seed demo posts/comments/votes/reactions into every EMPTY subtopic, "
        "and replies onto existing reply-less comments. Idempotent."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(1989)  # fixed seed -> reproducible content
        users, users_created = ensure_fake_users()

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

        n_replies = self._seed_replies(users)

        self.stdout.write(self.style.SUCCESS(
            f"Fake users: {users_created} created, {len(users)} total. "
            f"Subtopics: {filled} filled, {skipped} already had posts. "
            f"Created {n_posts} posts, {n_comments} comments, {n_votes} votes, "
            f"{n_reactions} reactions, {n_replies} replies."
        ))

    def _seed_replies(self, users) -> int:
        """Reply to a deterministic ~half of reply-less top-level comments.

        Each comment's selection and content are seeded by its pk, so re-runs
        are stable; the ``replies.exists()`` guard makes the pass idempotent —
        a comment never accrues a second round of seeded replies.
        """
        n_replies = 0
        top_level = Comment.objects.filter(
            parent__isnull=True, is_removed=False, is_deleted=False
        ).select_related("post")
        for comment in top_level:
            crng = random.Random(comment.pk)  # stable per comment
            if crng.random() >= REPLY_PROBABILITY:
                continue
            if comment.replies.exists():
                continue  # already has replies -> idempotent skip
            for _ in range(crng.randint(1, 2)):
                services.create_comment(
                    post=comment.post,
                    author=crng.choice(users),
                    body=crng.choice(REPLIES),
                    parent=comment,
                )
                n_replies += 1
        return n_replies
