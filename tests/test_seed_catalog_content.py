"""Tests for the ``seed_catalog_content`` demo-content seeder.

Verifies it seeds comments + one-level replies onto a deterministic sample of
published guitars (instruments) and gear pieces, authored by fake "{Name} Test"
users (never moderator/creator), and that it is idempotent.
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase

from catalog.models import Bridge, CatalogComment, GuitarModel, Nut, Pickup, Tuner

User = get_user_model()


class SeedCatalogContentTests(TestCase):
    def _seed_catalog(self):
        # The small illustrative catalog (6 guitars + gear), all published.
        call_command("seed_catalog", verbosity=0)

    def test_seeds_comments_and_replies_on_instruments_and_gear(self):
        self._seed_catalog()
        call_command("seed_catalog_content", verbosity=0)

        self.assertTrue(CatalogComment.objects.exists())
        # One-level replies were produced...
        replies = CatalogComment.objects.filter(parent__isnull=False)
        self.assertTrue(replies.exists())
        # ...and they are exactly one level deep.
        for reply in replies.select_related("parent")[:25]:
            self.assertIsNone(reply.parent.parent_id)

        guitar_ct = ContentType.objects.get_for_model(GuitarModel, for_concrete_model=False)
        gear_cts = [
            ContentType.objects.get_for_model(m, for_concrete_model=False)
            for m in (Bridge, Pickup, Tuner, Nut)
        ]
        # Both instruments and catalog pieces got commented on.
        self.assertTrue(CatalogComment.objects.filter(content_type=guitar_ct).exists())
        self.assertTrue(CatalogComment.objects.filter(content_type__in=gear_cts).exists())

    def test_rerun_is_idempotent(self):
        self._seed_catalog()
        call_command("seed_catalog_content", verbosity=0)
        comments = CatalogComment.objects.count()
        users = User.objects.count()
        self.assertGreater(comments, 0)

        call_command("seed_catalog_content", verbosity=0)
        self.assertEqual(CatalogComment.objects.count(), comments)
        self.assertEqual(User.objects.count(), users)

    def test_fake_users_never_moderator_or_creator(self):
        self._seed_catalog()
        call_command("seed_catalog_content", verbosity=0)
        fakes = User.objects.filter(username__endswith=" Test")
        self.assertTrue(fakes.exists())
        self.assertFalse(fakes.filter(is_community_moderator=True).exists())
        self.assertFalse(fakes.filter(is_riffhub_creator=True).exists())

    def test_item_with_existing_comment_is_skipped(self):
        self._seed_catalog()
        # Pre-comment a guitar that the every-Nth sampler would target (index 0).
        guitar = GuitarModel.objects.published().order_by("pk").first()
        author = User.objects.create_user(
            username="realuser", email="real@example.com", password="pw-12345",
            email_confirmed=True,
        )
        from catalog import services
        services.add_catalog_comment(target=guitar, author=author, body="first!")
        ct = ContentType.objects.get_for_model(GuitarModel, for_concrete_model=False)
        before = CatalogComment.objects.filter(content_type=ct, object_id=guitar.pk).count()

        call_command("seed_catalog_content", verbosity=0)

        after = CatalogComment.objects.filter(content_type=ct, object_id=guitar.pk).count()
        self.assertEqual(after, before)  # already had a comment -> untouched
