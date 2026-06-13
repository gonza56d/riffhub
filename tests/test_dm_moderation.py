"""Tests for DM reporting + moderation.

Contract (built in parallel):
  DirectMessage gains soft-remove (is_removed via core.Moderatable).
  DirectMessageReport(reporter, message, reason, status, handled_by, handled_at)
  services: report_message (participant-only, not own, dedups), open_reports,
            dismiss_report / remove_reported_message (moderator-gated)
  urls: messaging:report (message_id), messaging:reports, messaging:resolve_report
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from messaging import services as dm
from messaging.models import DirectMessage, DirectMessageReport, ReportStatus

User = get_user_model()


def make_user(name, **flags):
    u = User.objects.create_user(name, f"{name}@x.com", "pw12345!")
    for k, v in flags.items():
        setattr(u, k, v)
    if flags:
        u.save()
    return u


# ---------------------------------------------------------------------------
# report_message service
# ---------------------------------------------------------------------------
class ReportMessageServiceTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.msg = dm.send_message(self.alice, self.bob, "questionable content")

    def test_participant_can_report_other_message(self):
        report = dm.report_message(self.bob, self.msg, "this is abusive")
        self.assertEqual(report.reporter, self.bob)
        self.assertEqual(report.message, self.msg)
        self.assertEqual(report.status, ReportStatus.OPEN)

    def test_cannot_report_own_message(self):
        with self.assertRaises(PermissionDenied):
            dm.report_message(self.alice, self.msg, "reporting myself")

    def test_non_participant_cannot_report(self):
        carol = make_user("carol")
        with self.assertRaises(PermissionDenied):
            dm.report_message(carol, self.msg, "nosy")

    def test_blank_reason_rejected(self):
        with self.assertRaises(ValidationError):
            dm.report_message(self.bob, self.msg, "   ")

    def test_duplicate_open_report_deduped(self):
        r1 = dm.report_message(self.bob, self.msg, "first")
        r2 = dm.report_message(self.bob, self.msg, "second")
        self.assertEqual(r1.pk, r2.pk)
        self.assertEqual(
            DirectMessageReport.objects.filter(reporter=self.bob, message=self.msg).count(), 1
        )


# ---------------------------------------------------------------------------
# moderator resolution services
# ---------------------------------------------------------------------------
class ResolveReportServiceTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.mod = make_user("mod", is_community_moderator=True)
        self.msg = dm.send_message(self.alice, self.bob, "bad message")
        self.report = dm.report_message(self.bob, self.msg, "abuse")

    def test_open_reports_lists_open(self):
        self.assertIn(self.report, list(dm.open_reports()))

    def test_dismiss_sets_status_and_handler(self):
        dm.dismiss_report(self.mod, self.report)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.DISMISSED)
        self.assertEqual(self.report.handled_by, self.mod)
        self.assertIsNotNone(self.report.handled_at)

    def test_remove_soft_removes_message_and_actions_report(self):
        dm.remove_reported_message(self.mod, self.report, "illegal content")
        self.report.refresh_from_db()
        self.msg.refresh_from_db()
        self.assertTrue(self.msg.is_removed)
        self.assertEqual(self.msg.removed_by, self.mod)
        self.assertEqual(self.report.status, ReportStatus.ACTIONED)

    def test_dismiss_requires_moderator(self):
        with self.assertRaises(PermissionDenied):
            dm.dismiss_report(self.alice, self.report)

    def test_remove_requires_moderator(self):
        with self.assertRaises(PermissionDenied):
            dm.remove_reported_message(self.bob, self.report)


# ---------------------------------------------------------------------------
# report endpoint
# ---------------------------------------------------------------------------
class ReportViewTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.msg = dm.send_message(self.alice, self.bob, "report me")
        self.client = Client()
        self.url = reverse("messaging:report", args=[self.msg.pk])

    def test_requires_login(self):
        resp = self.client.post(self.url, {"reason": "x"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(DirectMessageReport.objects.count(), 0)

    def test_participant_reports_other_message(self):
        self.client.force_login(self.bob)
        resp = self.client.post(self.url, {"reason": "abusive"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Reported")
        self.assertEqual(DirectMessageReport.objects.count(), 1)

    def test_reporting_own_message_403(self):
        self.client.force_login(self.alice)
        resp = self.client.post(self.url, {"reason": "mine"})
        self.assertEqual(resp.status_code, 403)

    def test_non_participant_403(self):
        self.client.force_login(make_user("carol"))
        resp = self.client.post(self.url, {"reason": "nosy"})
        self.assertEqual(resp.status_code, 403)

    def test_blank_reason_400(self):
        self.client.force_login(self.bob)
        resp = self.client.post(self.url, {"reason": "  "})
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# moderator queue + resolution views
# ---------------------------------------------------------------------------
class ReportsQueueViewTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.mod = make_user("mod", is_community_moderator=True)
        self.msg = dm.send_message(self.alice, self.bob, "SECRET-REPORTED-BODY")
        self.report = dm.report_message(self.bob, self.msg, "abuse")
        self.client = Client()

    def test_queue_requires_login(self):
        resp = self.client.get(reverse("messaging:reports"))
        self.assertEqual(resp.status_code, 302)

    def test_queue_forbidden_for_non_moderator(self):
        self.client.force_login(self.alice)
        resp = self.client.get(reverse("messaging:reports"))
        self.assertEqual(resp.status_code, 403)

    def test_queue_shows_reported_content_to_moderator(self):
        self.client.force_login(self.mod)
        resp = self.client.get(reverse("messaging:reports"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "SECRET-REPORTED-BODY")  # privacy exception

    def test_dismiss_action(self):
        self.client.force_login(self.mod)
        resp = self.client.post(
            reverse("messaging:resolve_report", args=[self.report.pk, "dismiss"])
        )
        self.assertEqual(resp.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.DISMISSED)

    def test_remove_action(self):
        self.client.force_login(self.mod)
        self.client.post(
            reverse("messaging:resolve_report", args=[self.report.pk, "remove"]),
            {"reason": "illegal"},
        )
        self.report.refresh_from_db()
        self.msg.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.ACTIONED)
        self.assertTrue(self.msg.is_removed)

    def test_resolve_forbidden_for_non_moderator(self):
        self.client.force_login(self.alice)
        resp = self.client.post(
            reverse("messaging:resolve_report", args=[self.report.pk, "dismiss"])
        )
        self.assertEqual(resp.status_code, 403)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ReportStatus.OPEN)


# ---------------------------------------------------------------------------
# thread hides removed messages
# ---------------------------------------------------------------------------
class RemovedMessageInThreadTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.mod = make_user("mod", is_community_moderator=True)
        self.msg = dm.send_message(self.alice, self.bob, "BODY-TO-HIDE")
        report = dm.report_message(self.bob, self.msg, "abuse")
        dm.remove_reported_message(self.mod, report, "removed")
        self.client = Client()

    def test_removed_message_body_hidden_in_thread(self):
        self.client.force_login(self.bob)
        resp = self.client.get(reverse("messaging:thread", args=["alice"]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "BODY-TO-HIDE")
        self.assertContains(resp, "removed by a moderator")
