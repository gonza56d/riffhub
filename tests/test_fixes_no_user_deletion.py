"""Regression tests: users are never deletable.

Every authorship / ownership / participation / sanction-target FK that points
at the user is ``on_delete=PROTECT``, so a user who has authored a catalog
entry, written a post, sent a DM, or been banned cannot be deleted — they are
banned, never deleted, and their name always survives on their content.
"""

from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from django.test import TestCase

from catalog.constants import PublicationStatus
from catalog.models import Brand
from forum.models import Post, Subtopic, Topic
from messaging import services as dm
from moderation.models import Ban

User = get_user_model()


def make_user(name, **flags):
    return User.objects.create_user(name, f"{name}@x.com", "pw12345!", **flags)


class UsersAreNeverDeletableTests(TestCase):
    def test_cannot_delete_catalog_submitter(self):
        user = make_user("submitter")
        Brand.objects.create(
            name="ProtBrand", status=PublicationStatus.PUBLISHED, submitted_by=user
        )
        with self.assertRaises(ProtectedError):
            user.delete()
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_cannot_delete_post_author(self):
        user = make_user("poster")
        topic = Topic.objects.create(name="Gear")
        subtopic = Subtopic.objects.create(topic=topic, name="Guitars")
        Post.objects.create(subtopic=subtopic, author=user, title="Hi", body="Body")
        with self.assertRaises(ProtectedError):
            user.delete()
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_cannot_delete_dm_participants(self):
        sender = make_user("dm_sender")
        recipient = make_user("dm_recipient")
        dm.send_message(sender, recipient, "hello there")
        # The sender owns the message; both users are conversation participants.
        with self.assertRaises(ProtectedError):
            sender.delete()
        with self.assertRaises(ProtectedError):
            recipient.delete()

    def test_cannot_delete_a_banned_user(self):
        villain = make_user("villain")
        issuer = make_user("creator", is_riffhub_creator=True)
        Ban.objects.create(target=villain, issued_by=issuer, reason="spam")
        with self.assertRaises(ProtectedError):
            villain.delete()
        self.assertTrue(User.objects.filter(pk=villain.pk).exists())
