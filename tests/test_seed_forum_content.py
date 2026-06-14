"""Tests for the ``seed_forum_content`` demo-content seeder.

Verifies it fills empty subtopics with posts/comments/votes/reactions authored
by fake "{Name} Test" users (mixed levels, never moderator/creator), that it is
idempotent (safe to re-run — the live-deploy concern), and that it leaves
already-populated subtopics untouched.
"""

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models import Count
from django.test import TestCase

from forum.models import Comment, Post, Reaction, Subtopic, Vote

User = get_user_model()


class SeedForumContentTests(TestCase):
    def _seed_structure(self):
        call_command("seed_forum", verbosity=0)

    def test_fills_every_empty_subtopic_with_content(self):
        self._seed_structure()
        call_command("seed_forum_content", verbosity=0)

        # No predefined subtopic is left empty.
        empty = Subtopic.objects.annotate(n=Count("posts")).filter(n=0)
        self.assertFalse(empty.exists(), "every subtopic should have posts")

        # All four engagement kinds were produced.
        self.assertTrue(Post.objects.exists())
        self.assertTrue(Comment.objects.exists())
        self.assertTrue(Vote.objects.exists())
        self.assertTrue(Reaction.objects.exists())

    def test_fake_users_named_test_and_never_moderator_or_creator(self):
        self._seed_structure()
        call_command("seed_forum_content", verbosity=0)

        fakes = User.objects.filter(username__endswith=" Test")
        self.assertGreaterEqual(fakes.count(), 5)
        # The naming convention holds for every fake user.
        for u in fakes:
            self.assertTrue(u.username.endswith(" Test"))
        # The hard rule: no granted moderator/creator roles among them.
        self.assertFalse(fakes.filter(is_community_moderator=True).exists())
        self.assertFalse(fakes.filter(is_riffhub_creator=True).exists())
        # The level mix is present in the data: some founders, some with
        # accepted submissions (Collaborator-eligible), some plain Regulars.
        self.assertTrue(fakes.filter(is_founder=True).exists())
        self.assertTrue(fakes.filter(accepted_submissions_count__gt=0).exists())
        self.assertTrue(
            fakes.filter(is_founder=False, accepted_submissions_count=0).exists()
        )

    def test_rerun_is_idempotent(self):
        self._seed_structure()
        call_command("seed_forum_content", verbosity=0)
        posts, comments, votes, reactions, users = (
            Post.objects.count(),
            Comment.objects.count(),
            Vote.objects.count(),
            Reaction.objects.count(),
            User.objects.count(),
        )

        call_command("seed_forum_content", verbosity=0)

        self.assertEqual(Post.objects.count(), posts)
        self.assertEqual(Comment.objects.count(), comments)
        self.assertEqual(Vote.objects.count(), votes)
        self.assertEqual(Reaction.objects.count(), reactions)
        self.assertEqual(User.objects.count(), users)

    def test_subtopic_with_existing_posts_is_skipped(self):
        self._seed_structure()
        sub = Subtopic.objects.first()
        real = User.objects.create_user(
            username="realuser", email="real@example.com", password="pw-12345"
        )
        Post.objects.create(subtopic=sub, author=real, title="Real post", body="hi")

        call_command("seed_forum_content", verbosity=0)

        # The pre-populated subtopic keeps only its single real post.
        self.assertEqual(sub.posts.count(), 1)

    def test_market_subtopic_listings_have_prices(self):
        self._seed_structure()
        call_command("seed_forum_content", verbosity=0)
        market_posts = Post.objects.filter(subtopic__topic__is_market=True)
        self.assertTrue(market_posts.exists())
        for post in market_posts:
            self.assertIsNotNone(post.price)
