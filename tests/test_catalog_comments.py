"""Tests for catalog comments + one-level replies (guitar & gear detail pages).

Covers the ``CatalogComment`` model and the ``catalog.services`` comment helpers
— a system kept entirely separate from the forum ``Comment`` (Post-bound) so the
two domains' threads don't entangle.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from catalog import services
from catalog.constants import PublicationStatus
from catalog.models import Brand, CatalogComment, GuitarModel
from core.models import SiteConfiguration
from moderation import services as mod

User = get_user_model()


def make_user(username, **flags):
    return User.objects.create_user(
        username=username, email=f"{username}@example.com",
        password="pw-12345", email_confirmed=True, **flags,
    )


def make_guitar(brand, name):
    return GuitarModel.objects.create(
        brand=brand, name=name, num_strings=6,
        scale_length_min_inches=Decimal("25.5"),
        scale_length_max_inches=Decimal("25.5"),
        status=PublicationStatus.PUBLISHED,
    )


class CatalogCommentServiceTests(TestCase):
    def setUp(self):
        cfg = SiteConfiguration.get_solo()
        cfg.collaborator_promotion_threshold = 3
        cfg.founder_threshold = 30
        cfg.save()
        self.brand = Brand.objects.create(name="Acme", status=PublicationStatus.PUBLISHED)
        self.guitar = make_guitar(self.brand, "GTR-1")
        self.alice = make_user("alice")
        self.bob = make_user("bob")

    def test_add_top_level_comment(self):
        c = services.add_catalog_comment(target=self.guitar, author=self.alice, body="Nice!")
        self.assertEqual(c.target, self.guitar)
        self.assertIsNone(c.parent)
        self.assertEqual(CatalogComment.objects.count(), 1)

    def test_reply_attaches_to_parent(self):
        c = services.add_catalog_comment(target=self.guitar, author=self.alice, body="Question?")
        r = services.add_catalog_comment(target=self.guitar, author=self.bob, body="Answer.", parent=c)
        self.assertEqual(r.parent_id, c.pk)
        self.assertEqual(list(c.replies.all()), [r])

    def test_blank_body_rejected(self):
        with self.assertRaises(ValidationError):
            services.add_catalog_comment(target=self.guitar, author=self.alice, body="   ")

    def test_replies_are_one_level_only(self):
        c = services.add_catalog_comment(target=self.guitar, author=self.alice, body="top")
        r = services.add_catalog_comment(target=self.guitar, author=self.bob, body="reply", parent=c)
        with self.assertRaises(ValidationError):
            services.add_catalog_comment(
                target=self.guitar, author=self.alice, body="reply to reply", parent=r
            )

    def test_reply_parent_must_match_target(self):
        other = make_guitar(self.brand, "GTR-2")
        c = services.add_catalog_comment(target=self.guitar, author=self.alice, body="on g1")
        with self.assertRaises(ValidationError):
            services.add_catalog_comment(
                target=other, author=self.bob, body="cross-page", parent=c
            )

    def test_banned_user_cannot_comment(self):
        moderator = make_user("mod", is_community_moderator=True)
        mod.ban(moderator, self.bob, reason="spam")
        banned = User.objects.get(pk=self.bob.pk)
        with self.assertRaises(PermissionDenied):
            services.add_catalog_comment(target=self.guitar, author=banned, body="sneak in")

    def test_silenced_user_cannot_comment(self):
        moderator = make_user("mod2", is_community_moderator=True)
        mod.silence(moderator, self.bob, reason="cool off")
        silenced = User.objects.get(pk=self.bob.pk)
        with self.assertRaises(PermissionDenied):
            services.add_catalog_comment(target=self.guitar, author=silenced, body="muted")

    def test_thread_hides_removed_and_orders_newest_first(self):
        c1 = services.add_catalog_comment(target=self.guitar, author=self.alice, body="first")
        c2 = services.add_catalog_comment(target=self.guitar, author=self.bob, body="second")
        c1.is_removed = True
        c1.save(update_fields=["is_removed"])
        thread = list(services.catalog_comment_thread(self.guitar))
        self.assertEqual(thread, [c2])  # removed hidden; newest-first

    def test_thread_prefetches_visible_replies_oldest_first(self):
        c = services.add_catalog_comment(target=self.guitar, author=self.alice, body="top")
        r1 = services.add_catalog_comment(target=self.guitar, author=self.bob, body="r1", parent=c)
        r2 = services.add_catalog_comment(target=self.guitar, author=self.alice, body="r2", parent=c)
        r1.is_removed = True
        r1.save(update_fields=["is_removed"])
        thread = list(services.catalog_comment_thread(self.guitar))
        self.assertEqual(list(thread[0].replies.all()), [r2])  # removed reply hidden

    def test_comments_are_isolated_per_target(self):
        g2 = make_guitar(self.brand, "GTR-3")
        services.add_catalog_comment(target=self.guitar, author=self.alice, body="on g1")
        services.add_catalog_comment(target=g2, author=self.bob, body="on g3")
        self.assertEqual(services.catalog_comment_thread(self.guitar).count(), 1)
        self.assertEqual(services.catalog_comment_thread(g2).count(), 1)
