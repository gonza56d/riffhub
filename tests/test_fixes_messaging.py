"""Regression tests for confirmed messaging bugs.

Covers:
  #10/#23 — the inbox conversation-list preview must NOT leak the body of a
            moderator-removed direct message to either participant; it renders
            the "[removed by a moderator]" placeholder instead.
  n19     — unread_count() must exclude removed messages so a removed-but-unread
            DM no longer inflates the unread badge.
  #36/#37 — terminal-state guards: dismiss_report / remove_reported_message
            no-op once a report is handled, and report_message refuses to
            report an already-removed message.

Mirrors tests/test_messaging.py for style/imports.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase
from django.urls import reverse

from messaging import services as dm
from messaging.models import (
    Conversation,
    DirectMessage,
    DirectMessageReport,
    ReportStatus,
)

User = get_user_model()

REMOVED_PLACEHOLDER = "[removed by a moderator]"


def make_user(username, **flags):
    return User.objects.create_user(
        username, f"{username}@x.com", "pw12345!", **flags
    )


# ---------------------------------------------------------------------------
# #10/#23 — inbox preview must not leak a removed body
# ---------------------------------------------------------------------------
class InboxRemovedPreviewTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")
        self.mod = make_user("mod", is_community_moderator=True)
        self.client = Client()

    def _remove_only_message(self):
        msg = dm.send_message(self.b, self.a, "super secret leak")
        msg.mark_removed(by=self.mod, reason="abuse")
        return msg

    def test_inbox_hides_removed_body_for_recipient(self):
        self._remove_only_message()
        self.client.force_login(self.a)
        resp = self.client.get(reverse("messaging:inbox"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "super secret leak")
        self.assertContains(resp, REMOVED_PLACEHOLDER)

    def test_inbox_hides_removed_body_for_sender(self):
        self._remove_only_message()
        self.client.force_login(self.b)
        resp = self.client.get(reverse("messaging:inbox"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "super secret leak")
        self.assertContains(resp, REMOVED_PLACEHOLDER)

    def test_inbox_prefers_latest_non_removed_message(self):
        # Older message stays, newest is removed: preview shows the older body,
        # never the removed one, and no placeholder is needed.
        dm.send_message(self.b, self.a, "earlier visible message")
        newer = dm.send_message(self.b, self.a, "later removed message")
        newer.mark_removed(by=self.mod, reason="abuse")
        self.client.force_login(self.a)
        resp = self.client.get(reverse("messaging:inbox"))
        self.assertContains(resp, "earlier visible message")
        self.assertNotContains(resp, "later removed message")

    def test_inbox_rows_last_message_is_non_removed_when_available(self):
        dm.send_message(self.b, self.a, "kept")
        newer = dm.send_message(self.b, self.a, "gone")
        newer.mark_removed(by=self.mod, reason="abuse")
        rows = dm.inbox_rows(self.a)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["last_message"].body, "kept")


# ---------------------------------------------------------------------------
# n19 — unread_count must exclude removed messages
# ---------------------------------------------------------------------------
class UnreadCountExcludesRemovedTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")
        self.mod = make_user("mod", is_community_moderator=True)

    def test_removed_unread_message_not_counted(self):
        msg = dm.send_message(self.b, self.a, "spam")
        self.assertEqual(dm.unread_count(self.a), 1)
        msg.mark_removed(by=self.mod, reason="spam")
        self.assertEqual(dm.unread_count(self.a), 0)

    def test_removed_message_does_not_inflate_inbox_unread(self):
        msg = dm.send_message(self.b, self.a, "spam")
        msg.mark_removed(by=self.mod, reason="spam")
        rows = dm.inbox_rows(self.a)
        self.assertEqual(rows[0]["unread"], 0)

    def test_non_removed_unread_still_counted(self):
        removed = dm.send_message(self.b, self.a, "spam")
        removed.mark_removed(by=self.mod, reason="spam")
        dm.send_message(self.b, self.a, "legit unread")
        self.assertEqual(dm.unread_count(self.a), 1)


# ---------------------------------------------------------------------------
# #36/#37 — report terminal-state guards
# ---------------------------------------------------------------------------
class ReportTerminalStateTests(TestCase):
    def setUp(self):
        self.a = make_user("alice")
        self.b = make_user("bob")
        self.mod = make_user("mod", is_community_moderator=True)
        # bob sends; alice (recipient) reports.
        self.message = dm.send_message(self.b, self.a, "reportable message")
        self.report = dm.report_message(self.a, self.message, "abusive")

    def test_dismiss_after_actioned_is_noop(self):
        dm.remove_reported_message(self.mod, self.report, reason="abuse")
        self.report.refresh_from_db()
        self.message.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.ACTIONED)
        self.assertTrue(self.message.is_removed)

        dm.dismiss_report(self.mod, self.report)

        self.report.refresh_from_db()
        self.message.refresh_from_db()
        # Status must NOT flip to dismissed and the message stays removed.
        self.assertEqual(self.report.status, ReportStatus.ACTIONED)
        self.assertTrue(self.message.is_removed)

    def test_remove_after_dismissed_is_noop(self):
        dm.dismiss_report(self.mod, self.report)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.DISMISSED)

        dm.remove_reported_message(self.mod, self.report, reason="abuse")

        self.report.refresh_from_db()
        self.message.refresh_from_db()
        # A dismissed report can't be re-actioned; message stays present.
        self.assertEqual(self.report.status, ReportStatus.DISMISSED)
        self.assertFalse(self.message.is_removed)

    def test_reporting_removed_message_refused(self):
        dm.remove_reported_message(self.mod, self.report, reason="abuse")
        self.message.refresh_from_db()
        # Reporting an already-removed message is refused for the recipient.
        with self.assertRaises(PermissionDenied):
            dm.report_message(self.a, self.message, "still bad")
        # No new report spawned for the removed message.
        self.assertEqual(
            DirectMessageReport.objects.filter(message=self.message).count(), 1
        )

    def test_removed_message_not_in_open_reports_queue(self):
        dm.remove_reported_message(self.mod, self.report, reason="abuse")
        self.message.refresh_from_db()
        # Attempt to re-report (refused), and confirm the queue stays empty of
        # any fresh item for this already-handled message.
        with self.assertRaises(PermissionDenied):
            dm.report_message(self.a, self.message, "again")
        self.assertNotIn(self.message.pk, [r.message_id for r in dm.open_reports()])


# ---------------------------------------------------------------------------
# A report against a moderator is visible/handleable ONLY by Riffhub Creators
# (a moderator must never see or act on a report about themselves or a peer).
# ---------------------------------------------------------------------------
class ReportAgainstModeratorIsCreatorOnlyTests(TestCase):
    def setUp(self):
        self.mod = make_user("mod", is_community_moderator=True)
        self.peer_mod = make_user("peer_mod", is_community_moderator=True)
        self.creator = make_user("creator", is_riffhub_creator=True)
        self.regular = make_user("regular")
        self.client = Client()
        # A report filed AGAINST the moderator (the mod is the message sender).
        mod_msg = dm.send_message(self.mod, self.regular, "questionable mod message")
        self.report_vs_mod = dm.report_message(
            self.regular, mod_msg, "a moderator misbehaved"
        )
        # An ordinary report against a regular user's message.
        reg_msg = dm.send_message(self.regular, self.peer_mod, "ordinary bad message")
        self.report_vs_regular = dm.report_message(self.peer_mod, reg_msg, "spam")

    # open_reports() visibility ---------------------------------------------
    def test_moderator_cannot_see_report_against_self(self):
        visible = dm.open_reports(self.mod)
        self.assertIn(self.report_vs_regular, visible)
        self.assertNotIn(self.report_vs_mod, visible)

    def test_peer_moderator_cannot_see_report_against_a_moderator(self):
        self.assertNotIn(self.report_vs_mod, dm.open_reports(self.peer_mod))

    def test_creator_sees_all_reports_including_against_moderators(self):
        visible = dm.open_reports(self.creator)
        self.assertIn(self.report_vs_mod, visible)
        self.assertIn(self.report_vs_regular, visible)

    # the moderation queue view ---------------------------------------------
    def test_queue_hides_mod_report_body_from_a_moderator(self):
        self.client.force_login(self.mod)
        resp = self.client.get(reverse("messaging:reports"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "questionable mod message")
        self.assertContains(resp, "ordinary bad message")

    def test_queue_shows_mod_report_to_a_creator(self):
        self.client.force_login(self.creator)
        resp = self.client.get(reverse("messaging:reports"))
        self.assertContains(resp, "questionable mod message")

    # resolution guard -------------------------------------------------------
    def test_moderator_cannot_dismiss_report_against_a_moderator(self):
        with self.assertRaises(PermissionDenied):
            dm.dismiss_report(self.peer_mod, self.report_vs_mod)
        self.report_vs_mod.refresh_from_db()
        self.assertEqual(self.report_vs_mod.status, ReportStatus.OPEN)

    def test_creator_can_dismiss_report_against_a_moderator(self):
        dm.dismiss_report(self.creator, self.report_vs_mod)
        self.report_vs_mod.refresh_from_db()
        self.assertEqual(self.report_vs_mod.status, ReportStatus.DISMISSED)

    def test_resolve_view_forbids_a_moderator_on_a_mod_report(self):
        self.client.force_login(self.peer_mod)
        resp = self.client.post(
            reverse("messaging:resolve_report", args=[self.report_vs_mod.pk, "dismiss"])
        )
        self.assertEqual(resp.status_code, 403)
        self.report_vs_mod.refresh_from_db()
        self.assertEqual(self.report_vs_mod.status, ReportStatus.OPEN)
