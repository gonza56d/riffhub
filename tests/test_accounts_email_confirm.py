"""Tests for the e-mail confirmation lifecycle (``accounts``).

Covers the ``EmailConfirmation`` model, the ``confirm_email`` /
``resend_confirmation`` views, the sign-up flow that mints a token, and the
PRODUCT.md rule that confirming e-mail unlocks collab-db submissions
(``catalog.services.can_submit_to_collab``).

Per PRODUCT.md:
  * "Who can upload stuff to the collab-db: Anyone that has signed up and
    confirmed email."
  * Confirming an e-mail is what unlocks collab-db contributions; forum
    participation does NOT require it (the user is logged in on sign-up).
"""

import uuid

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import EmailConfirmation
from catalog.services import can_submit_to_collab
from core.models import SiteConfiguration

User = get_user_model()


def make_user(username="riffer", *, email=None, email_confirmed=False, password="sup3r-s3cret-pw!"):
    """Create a user with sensible defaults for these tests."""
    return User.objects.create_user(
        username=username,
        email=email or f"{username}@example.com",
        password=password,
        email_confirmed=email_confirmed,
    )


class EmailConfirmationModelTests(TestCase):
    """The ``EmailConfirmation`` model: token defaults + ``confirm()``."""

    def test_new_confirmation_gets_uuid_token_and_is_pending(self):
        user = make_user()
        confirmation = EmailConfirmation.objects.create(user=user)

        self.assertIsInstance(confirmation.token, uuid.UUID)
        self.assertIsNone(confirmation.confirmed_at)
        # Pending: the owning user is not yet confirmed.
        user.refresh_from_db()
        self.assertFalse(user.email_confirmed)

    def test_tokens_are_unique_per_confirmation(self):
        user = make_user()
        first = EmailConfirmation.objects.create(user=user)
        second = EmailConfirmation.objects.create(user=user)
        self.assertNotEqual(first.token, second.token)

    def test_confirm_sets_timestamp_and_flags_user(self):
        user = make_user()
        confirmation = EmailConfirmation.objects.create(user=user)

        confirmation.confirm()

        confirmation.refresh_from_db()
        user.refresh_from_db()
        self.assertIsNotNone(confirmation.confirmed_at)
        self.assertTrue(user.email_confirmed)

    def test_confirm_without_save_does_not_persist(self):
        user = make_user()
        confirmation = EmailConfirmation.objects.create(user=user)

        confirmation.confirm(save=False)

        # In-memory state is mutated...
        self.assertIsNotNone(confirmation.confirmed_at)
        self.assertTrue(confirmation.user.email_confirmed)
        # ...but nothing was written.
        confirmation.refresh_from_db()
        user.refresh_from_db()
        self.assertIsNone(confirmation.confirmed_at)
        self.assertFalse(user.email_confirmed)

    def test_str_reflects_pending_then_confirmed(self):
        user = make_user(username="strummer")
        confirmation = EmailConfirmation.objects.create(user=user)
        self.assertIn("pending", str(confirmation))
        confirmation.confirm()
        self.assertIn("confirmed", str(confirmation))

    def test_ordering_is_most_recent_first(self):
        user = make_user()
        old = EmailConfirmation.objects.create(user=user)
        new = EmailConfirmation.objects.create(user=user)
        ordered = list(EmailConfirmation.objects.filter(user=user))
        self.assertEqual(ordered[0], new)
        self.assertEqual(ordered[1], old)


class SignupSendsConfirmationTests(TestCase):
    """Sign-up creates a fresh EmailConfirmation token and mails the link.

    Sign-up logs the user in immediately (forum participation does not require
    confirmation) but the e-mail is still UNconfirmed until the link is hit.
    """

    def _signup_payload(self, username="newbie", email="newbie@example.com"):
        return {
            "username": username,
            "email": email,
            "password1": "an0ther-s3cret-pw!",
            "password2": "an0ther-s3cret-pw!",
        }

    def test_signup_creates_unconfirmed_email_confirmation(self):
        mail.outbox = []
        resp = self.client.post(reverse("signup"), self._signup_payload())
        self.assertEqual(resp.status_code, 200)

        user = User.objects.get(username="newbie")
        self.assertFalse(user.email_confirmed)

        confirmations = EmailConfirmation.objects.filter(user=user)
        self.assertEqual(confirmations.count(), 1)
        confirmation = confirmations.get()
        self.assertIsInstance(confirmation.token, uuid.UUID)
        self.assertIsNone(confirmation.confirmed_at)

    def test_signup_sends_confirmation_email_to_the_user(self):
        mail.outbox = []
        self.client.post(reverse("signup"), self._signup_payload())

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["newbie@example.com"])
        # The mailed body carries the confirmation link with the real token.
        confirmation = EmailConfirmation.objects.get(user__username="newbie")
        self.assertIn(str(confirmation.token), sent.body)

    def test_signup_logs_the_new_user_in(self):
        self.client.post(reverse("signup"), self._signup_payload())
        # The session now belongs to the freshly created user.
        user = User.objects.get(username="newbie")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)


class ConfirmEmailViewTests(TestCase):
    """GET /accounts/confirm/<token>/ — happy path, idempotency, 404."""

    def setUp(self):
        self.user = make_user()
        self.confirmation = EmailConfirmation.objects.create(user=self.user)

    def test_get_confirms_email_and_redirects_to_forum(self):
        resp = self.client.get(
            reverse("confirm_email", args=[self.confirmation.token])
        )
        self.assertRedirects(
            resp, reverse("forum:index"), fetch_redirect_response=False
        )

        self.user.refresh_from_db()
        self.confirmation.refresh_from_db()
        self.assertTrue(self.user.email_confirmed)
        self.assertIsNotNone(self.confirmation.confirmed_at)

    def test_first_visit_shows_success_message(self):
        resp = self.client.get(
            reverse("confirm_email", args=[self.confirmation.token]),
            follow=True,
        )
        msgs = [m.message for m in resp.context["messages"]]
        self.assertTrue(any("confirmed" in m for m in msgs))
        self.assertFalse(any("already" in m for m in msgs))

    def test_second_visit_is_idempotent_and_reports_already_confirmed(self):
        url = reverse("confirm_email", args=[self.confirmation.token])

        # First visit confirms.
        self.client.get(url)
        self.confirmation.refresh_from_db()
        first_confirmed_at = self.confirmation.confirmed_at
        self.assertIsNotNone(first_confirmed_at)

        # Second visit must NOT re-stamp confirmed_at and must say "already".
        resp = self.client.get(url, follow=True)
        self.confirmation.refresh_from_db()
        self.assertEqual(self.confirmation.confirmed_at, first_confirmed_at)

        msgs = [m.message for m in resp.context["messages"]]
        self.assertTrue(any("already" in m for m in msgs))

        # User stays confirmed.
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_confirmed)

    def test_unknown_token_returns_404(self):
        bogus = uuid.uuid4()
        # Guard against an astronomically unlikely collision with setUp's token.
        self.assertFalse(EmailConfirmation.objects.filter(token=bogus).exists())
        resp = self.client.get(reverse("confirm_email", args=[bogus]))
        self.assertEqual(resp.status_code, 404)

    def test_confirming_one_token_confirms_the_user_globally(self):
        """A user may have several pending tokens; confirming any one flips the
        user's ``email_confirmed`` flag (the others simply remain unconfirmed
        rows but the user is confirmed)."""
        other = EmailConfirmation.objects.create(user=self.user)
        self.client.get(reverse("confirm_email", args=[other.token]))

        self.user.refresh_from_db()
        self.assertTrue(self.user.email_confirmed)
        other.refresh_from_db()
        self.assertIsNotNone(other.confirmed_at)
        # The first token was never visited, so it stays pending.
        self.confirmation.refresh_from_db()
        self.assertIsNone(self.confirmation.confirmed_at)


class ResendConfirmationViewTests(TestCase):
    """POST /accounts/resend-confirmation/ — fresh token for unconfirmed,
    no-op for confirmed / anonymous."""

    def test_anonymous_post_is_noop(self):
        mail.outbox = []
        resp = self.client.post(reverse("resend_confirmation"))
        # Redirects (back to referer or forum index) and creates nothing.
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(EmailConfirmation.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_logged_in_unconfirmed_user_gets_a_fresh_token(self):
        user = make_user(email_confirmed=False)
        self.client.force_login(user)
        mail.outbox = []

        resp = self.client.post(reverse("resend_confirmation"))
        self.assertEqual(resp.status_code, 302)

        confirmations = EmailConfirmation.objects.filter(user=user)
        self.assertEqual(confirmations.count(), 1)
        self.assertIsNone(confirmations.get().confirmed_at)
        # A confirmation e-mail was actually sent.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])

    def test_resend_mints_a_distinct_new_token_each_time(self):
        user = make_user(email_confirmed=False)
        existing = EmailConfirmation.objects.create(user=user)
        self.client.force_login(user)

        self.client.post(reverse("resend_confirmation"))

        tokens = set(
            EmailConfirmation.objects.filter(user=user).values_list("token", flat=True)
        )
        self.assertEqual(len(tokens), 2)
        self.assertIn(existing.token, tokens)

    def test_resend_is_noop_for_already_confirmed_user(self):
        user = make_user(email_confirmed=True)
        self.client.force_login(user)
        mail.outbox = []

        resp = self.client.post(reverse("resend_confirmation"))
        self.assertEqual(resp.status_code, 302)
        # No new token, no e-mail.
        self.assertEqual(EmailConfirmation.objects.filter(user=user).count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_requires_post(self):
        user = make_user(email_confirmed=False)
        self.client.force_login(user)
        # ``require_POST`` -> 405 on GET, and nothing minted.
        resp = self.client.get(reverse("resend_confirmation"))
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(EmailConfirmation.objects.filter(user=user).count(), 0)

    def test_resend_honours_referer_redirect(self):
        user = make_user(email_confirmed=False)
        self.client.force_login(user)
        referer = "/forum/post/1/"
        resp = self.client.post(
            reverse("resend_confirmation"), HTTP_REFERER=referer
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], referer)


class CanSubmitToCollabConfirmationGateTests(TestCase):
    """PRODUCT.md: only signed-up *and e-mail-confirmed* users may submit to
    the collab-db. The confirmation flag is the gate."""

    def setUp(self):
        # can_submit_to_collab reads max_rejected_before_cooldown (a defaulted
        # field), so the singleton's defaults are sufficient. We do NOT set the
        # promotion thresholds here — submitting does not require Collaborator.
        self.config = SiteConfiguration.get_solo()
        # Sanity: default cooldown threshold present.
        self.assertEqual(self.config.max_rejected_before_cooldown, 3)

    def test_false_before_confirmation(self):
        user = make_user(email_confirmed=False)
        self.assertFalse(can_submit_to_collab(user))

    def test_true_after_confirmation_no_rejections(self):
        user = make_user(email_confirmed=True)
        self.assertTrue(can_submit_to_collab(user))

    def test_confirming_via_view_flips_the_gate(self):
        """End-to-end: a fresh unconfirmed user cannot submit; after hitting the
        confirmation link they can (no rejection cooldown in play)."""
        user = make_user(email_confirmed=False)
        confirmation = EmailConfirmation.objects.create(user=user)
        self.assertFalse(can_submit_to_collab(user))

        self.client.get(reverse("confirm_email", args=[confirmation.token]))

        user.refresh_from_db()
        self.assertTrue(user.email_confirmed)
        self.assertTrue(can_submit_to_collab(user))

    def test_confirmed_user_at_cooldown_boundary_still_allowed(self):
        """Block is strictly ``rejected_count > threshold``; equal to the
        threshold is still allowed."""
        user = make_user(email_confirmed=True)
        user.rejected_submissions_count = self.config.max_rejected_before_cooldown
        user.save(update_fields=["rejected_submissions_count"])
        self.assertTrue(can_submit_to_collab(user))

    def test_confirmed_user_over_cooldown_is_blocked(self):
        user = make_user(email_confirmed=True)
        user.rejected_submissions_count = self.config.max_rejected_before_cooldown + 1
        user.save(update_fields=["rejected_submissions_count"])
        self.assertFalse(can_submit_to_collab(user))

    def test_unconfirmed_with_no_rejections_still_blocked(self):
        """Confirmation is mandatory even with a clean rejection record."""
        user = make_user(email_confirmed=False)
        self.assertEqual(user.rejected_submissions_count, 0)
        self.assertFalse(can_submit_to_collab(user))
