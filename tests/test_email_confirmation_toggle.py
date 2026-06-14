"""Tests for the env-toggleable e-mail confirmation gate.

The feature is controlled by ``REQUIRE_EMAIL_CONFIRMATION`` (default ``True``).
When disabled, sign-up auto-confirms the user and sends no e-mail, and the
collab-db submission gate (``can_submit_to_collab``) ignores ``email_confirmed``
— the confirmation code stays in place, just dormant.
"""

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import EmailConfirmation
from accounts.services import email_confirmation_required, has_confirmed_email
from catalog.services import can_submit_to_collab

User = get_user_model()

STRONG_PASSWORD = "Vintage-Walnut-42!"


def _payload(**overrides):
    data = {
        "username": "toggletester",
        "email": "toggle@riffhub.test",
        "password1": STRONG_PASSWORD,
        "password2": STRONG_PASSWORD,
    }
    data.update(overrides)
    return data


@override_settings(REQUIRE_EMAIL_CONFIRMATION=False)
class ConfirmationDisabledTests(TestCase):
    """With the feature off, sign-up auto-confirms and the gate is open."""

    def test_helper_reports_not_required(self):
        self.assertFalse(email_confirmation_required())

    def test_signup_auto_confirms_and_redirects_into_the_app(self):
        resp = self.client.post(reverse("signup"), _payload())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("forum:index"))
        user = User.objects.get(username="toggletester")
        self.assertTrue(user.email_confirmed)

    def test_signup_sends_no_email_and_creates_no_token(self):
        self.client.post(reverse("signup"), _payload())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(EmailConfirmation.objects.count(), 0)

    def test_gate_is_open_even_for_an_unconfirmed_user(self):
        user = User.objects.create_user(
            username="unconfirmed", email="u@riffhub.test", password=STRONG_PASSWORD
        )
        self.assertFalse(user.email_confirmed)
        self.assertTrue(has_confirmed_email(user))
        self.assertTrue(can_submit_to_collab(user))


@override_settings(REQUIRE_EMAIL_CONFIRMATION=True)
class ConfirmationRequiredTests(TestCase):
    """Default behaviour: confirmation is required (token + e-mail, gate closed)."""

    def test_helper_reports_required(self):
        self.assertTrue(email_confirmation_required())

    def test_signup_sends_email_and_creates_pending_token(self):
        resp = self.client.post(reverse("signup"), _payload())
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "registration/signup_done.html")
        self.assertEqual(len(mail.outbox), 1)
        user = User.objects.get(username="toggletester")
        self.assertFalse(user.email_confirmed)
        self.assertEqual(EmailConfirmation.objects.filter(user=user).count(), 1)

    def test_gate_is_closed_for_an_unconfirmed_user(self):
        user = User.objects.create_user(
            username="unconfirmed", email="u@riffhub.test", password=STRONG_PASSWORD
        )
        self.assertFalse(has_confirmed_email(user))
        self.assertFalse(can_submit_to_collab(user))
