"""Tests for the direct-messages (DMs) feature.

Written against the agreed contract while the feature is built in parallel:
  models   : Conversation (canonical 1:1, for_pair/other/involves), DirectMessage
  services : get_conversation, send_message, mark_read, unread_count, inbox_rows
  views    : messaging:inbox, messaging:thread (username), messaging:send (username)
  gating   : login required; silenced/banned can't send; no self-DM; no empty body
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from messaging import services as dm
from messaging.context_processors import messaging_flags
from messaging.models import Conversation, DirectMessage
from moderation.models import Silence

User = get_user_model()


def make_user(username):
    return User.objects.create_user(username, f"{username}@x.com", "pw12345!")


def silence(user):
    """Give the user an active permanent silence (so can_participate is False)."""
    return Silence.objects.create(
        target=user, reason="threats", sequence=1, is_permanent=True, is_public_flag=True
    )


# ---------------------------------------------------------------------------
# Conversation model
# ---------------------------------------------------------------------------
class ConversationModelTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")

    def test_for_pair_canonicalises_order(self):
        conv, created = Conversation.for_pair(self.a, self.b)
        self.assertTrue(created)
        # lower pk is user_low regardless of argument order
        lo, hi = sorted([self.a, self.b], key=lambda u: u.pk)
        self.assertEqual(conv.user_low_id, lo.pk)
        self.assertEqual(conv.user_high_id, hi.pk)

    def test_for_pair_is_idempotent_both_directions(self):
        first, c1 = Conversation.for_pair(self.a, self.b)
        second, c2 = Conversation.for_pair(self.b, self.a)
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Conversation.objects.count(), 1)

    def test_other_returns_the_counterpart(self):
        conv, _ = Conversation.for_pair(self.a, self.b)
        self.assertEqual(conv.other(self.a), self.b)
        self.assertEqual(conv.other(self.b), self.a)

    def test_involves(self):
        conv, _ = Conversation.for_pair(self.a, self.b)
        c = make_user("carol")
        self.assertTrue(conv.involves(self.a))
        self.assertTrue(conv.involves(self.b))
        self.assertFalse(conv.involves(c))


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------
class SendMessageServiceTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")

    def test_send_creates_message_and_conversation(self):
        msg = dm.send_message(self.a, self.b, "hey there")
        self.assertEqual(msg.sender, self.a)
        self.assertEqual(msg.body, "hey there")
        self.assertFalse(msg.is_read)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertTrue(msg.conversation.involves(self.b))

    def test_send_reuses_existing_conversation(self):
        dm.send_message(self.a, self.b, "one")
        dm.send_message(self.b, self.a, "two")
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(DirectMessage.objects.count(), 2)

    def test_send_sets_last_message_at(self):
        conv, _ = Conversation.for_pair(self.a, self.b)
        self.assertIsNone(conv.last_message_at)
        dm.send_message(self.a, self.b, "tick")
        conv.refresh_from_db()
        self.assertIsNotNone(conv.last_message_at)

    def test_cannot_message_self(self):
        with self.assertRaises(PermissionDenied):
            dm.send_message(self.a, self.a, "talking to myself")

    def test_blank_body_rejected(self):
        with self.assertRaises(ValidationError):
            dm.send_message(self.a, self.b, "   ")
        self.assertEqual(DirectMessage.objects.count(), 0)

    def test_silenced_sender_blocked(self):
        silence(self.a)
        with self.assertRaises(PermissionDenied):
            dm.send_message(self.a, self.b, "I am muted")

    def test_banned_sender_blocked(self):
        self.a.is_active = False
        self.a.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            dm.send_message(self.a, self.b, "I am banned")

    def test_silenced_user_can_still_receive(self):
        silence(self.b)
        msg = dm.send_message(self.a, self.b, "you can still read this")
        self.assertEqual(msg.body, "you can still read this")


# ---------------------------------------------------------------------------
# mark_read / unread_count
# ---------------------------------------------------------------------------
class ReadStateServiceTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")

    def test_unread_count_counts_incoming_only(self):
        dm.send_message(self.b, self.a, "hi alice")        # unread for a
        dm.send_message(self.b, self.a, "you there?")      # unread for a
        dm.send_message(self.a, self.b, "hi bob")          # unread for b, not a
        self.assertEqual(dm.unread_count(self.a), 2)
        self.assertEqual(dm.unread_count(self.b), 1)

    def test_mark_read_clears_incoming_not_own(self):
        dm.send_message(self.b, self.a, "incoming")
        dm.send_message(self.a, self.b, "outgoing")
        conv, _ = Conversation.for_pair(self.a, self.b)
        dm.mark_read(self.a, conv)
        self.assertEqual(dm.unread_count(self.a), 0)
        # b's incoming (a's outgoing) is still unread for b
        self.assertEqual(dm.unread_count(self.b), 1)

    def test_mark_read_noop_for_non_participant(self):
        c = make_user("carol")
        dm.send_message(self.b, self.a, "private")
        conv, _ = Conversation.for_pair(self.a, self.b)
        dm.mark_read(c, conv)  # carol isn't in this conversation
        self.assertEqual(dm.unread_count(self.a), 1)

    def test_unread_count_zero_for_anonymous(self):
        self.assertEqual(dm.unread_count(AnonymousUser()), 0)


# ---------------------------------------------------------------------------
# inbox_rows
# ---------------------------------------------------------------------------
class InboxRowsServiceTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")
        self.c = make_user("carol")

    def test_rows_structure_and_unread(self):
        dm.send_message(self.b, self.a, "from bob")
        rows = dm.inbox_rows(self.a)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(set(row), {"conversation", "other", "last_message", "unread"})
        self.assertEqual(row["other"], self.b)
        self.assertEqual(row["last_message"].body, "from bob")
        self.assertEqual(row["unread"], 1)

    def test_rows_ordered_by_recency(self):
        dm.send_message(self.b, self.a, "older")
        dm.send_message(self.c, self.a, "newer")
        rows = dm.inbox_rows(self.a)
        self.assertEqual([r["other"] for r in rows], [self.c, self.b])

    def test_rows_empty_for_user_without_conversations(self):
        self.assertEqual(dm.inbox_rows(make_user("lonely")), [])


# ---------------------------------------------------------------------------
# context processor
# ---------------------------------------------------------------------------
class ContextProcessorTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.a = make_user("alice")
        self.b = make_user("bob")

    def test_unread_dm_count_for_authenticated(self):
        dm.send_message(self.b, self.a, "ping")
        req = self.rf.get("/")
        req.user = self.a
        self.assertEqual(messaging_flags(req)["unread_dm_count"], 1)

    def test_unread_dm_count_zero_for_anonymous(self):
        req = self.rf.get("/")
        req.user = AnonymousUser()
        self.assertEqual(messaging_flags(req)["unread_dm_count"], 0)


# ---------------------------------------------------------------------------
# Views: inbox / thread / send
# ---------------------------------------------------------------------------
class InboxViewTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")
        self.client = Client()

    def test_requires_login(self):
        resp = self.client.get(reverse("messaging:inbox"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_lists_conversations(self):
        dm.send_message(self.b, self.a, "hello alice")
        self.client.force_login(self.a)
        resp = self.client.get(reverse("messaging:inbox"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "bob")


class ThreadViewTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")
        self.client = Client()

    def test_requires_login(self):
        resp = self.client.get(reverse("messaging:thread", args=["bob"]))
        self.assertEqual(resp.status_code, 302)

    def test_get_or_create_and_render(self):
        self.client.force_login(self.a)
        resp = self.client.get(reverse("messaging:thread", args=["bob"]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Conversation.objects.filter().exists())

    def test_renders_messages(self):
        dm.send_message(self.b, self.a, "a message body")
        self.client.force_login(self.a)
        resp = self.client.get(reverse("messaging:thread", args=["bob"]))
        self.assertContains(resp, "a message body")

    def test_viewing_marks_incoming_read(self):
        dm.send_message(self.b, self.a, "unread until viewed")
        self.assertEqual(dm.unread_count(self.a), 1)
        self.client.force_login(self.a)
        self.client.get(reverse("messaging:thread", args=["bob"]))
        self.assertEqual(dm.unread_count(self.a), 0)

    def test_self_thread_redirects(self):
        self.client.force_login(self.a)
        resp = self.client.get(reverse("messaging:thread", args=["alice"]))
        self.assertEqual(resp.status_code, 302)

    def test_unknown_user_404(self):
        self.client.force_login(self.a)
        resp = self.client.get(reverse("messaging:thread", args=["nobody"]))
        self.assertEqual(resp.status_code, 404)

    def test_silenced_user_cannot_send_from_thread(self):
        silence(self.a)
        self.client.force_login(self.a)
        resp = self.client.get(reverse("messaging:thread", args=["bob"]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["can_send"])


class SendViewTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")
        self.client = Client()

    def test_requires_login(self):
        resp = self.client.post(reverse("messaging:send", args=["bob"]), {"body": "hi"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(DirectMessage.objects.count(), 0)

    def test_send_creates_and_returns_fragment(self):
        self.client.force_login(self.a)
        resp = self.client.post(reverse("messaging:send", args=["bob"]), {"body": "hi bob"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "hi bob")
        self.assertEqual(DirectMessage.objects.count(), 1)

    def test_empty_body_400(self):
        self.client.force_login(self.a)
        resp = self.client.post(reverse("messaging:send", args=["bob"]), {"body": "  "})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(DirectMessage.objects.count(), 0)

    def test_send_to_self_403(self):
        self.client.force_login(self.a)
        resp = self.client.post(reverse("messaging:send", args=["alice"]), {"body": "me"})
        self.assertEqual(resp.status_code, 403)

    def test_silenced_send_403(self):
        silence(self.a)
        self.client.force_login(self.a)
        resp = self.client.post(reverse("messaging:send", args=["bob"]), {"body": "muted"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(DirectMessage.objects.count(), 0)

    def test_get_not_allowed(self):
        self.client.force_login(self.a)
        resp = self.client.get(reverse("messaging:send", args=["bob"]))
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# Integration into existing pages (profile button, nav link)
# ---------------------------------------------------------------------------
class ProfileAndNavIntegrationTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")
        self.client = Client()

    def test_profile_shows_message_button_for_other_user(self):
        self.client.force_login(self.a)
        resp = self.client.get(reverse("profile", args=["bob"]))
        self.assertContains(resp, reverse("messaging:thread", args=["bob"]))

    def test_profile_hides_message_button_on_own_profile(self):
        self.client.force_login(self.a)
        resp = self.client.get(reverse("profile", args=["alice"]))
        self.assertNotContains(resp, reverse("messaging:thread", args=["alice"]))

    def test_nav_shows_messages_link_when_authenticated(self):
        self.client.force_login(self.a)
        resp = self.client.get(reverse("messaging:inbox"))
        self.assertContains(resp, reverse("messaging:inbox"))
