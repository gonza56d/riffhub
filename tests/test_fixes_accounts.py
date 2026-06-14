"""Regression tests for confirmed accounts bugs.

Covers:
  * #13 — case-variant e-mails must not create duplicate accounts; the signup
    form lowercases the whole address and rejects case-insensitive dupes.
  * #30/#38 — ``resend_confirmation`` must not honour an external Referer (open
    redirect); it falls back to the forum index for off-host referers.
"""

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from accounts.forms import SignUpForm
from accounts.models import EmailConfirmation

User = get_user_model()


# A password that comfortably clears every configured validator.
STRONG_PASSWORD = "Vintage-Walnut-42!"


def _valid_payload(**overrides):
    data = {
        "username": "stratlover",
        "email": "strat@riffhub.test",
        "password1": STRONG_PASSWORD,
        "password2": STRONG_PASSWORD,
    }
    data.update(overrides)
    return data


class CaseVariantEmailDuplicateTests(TestCase):
    """#13 — an existing e-mail blocks a case-variant signup (no 2nd account)."""

    def setUp(self):
        self.url = reverse("signup")
        self.existing = User.objects.create_user(
            username="rt_user",
            email="rt_user@example.com",
            password=STRONG_PASSWORD,
        )

    def test_uppercased_variant_of_existing_email_is_rejected(self):
        # RT_USER@EXAMPLE.COM differs only in case from the existing address.
        resp = self.client.post(
            self.url,
            _valid_payload(username="impostor", email="RT_USER@EXAMPLE.COM"),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["form"].is_valid())
        self.assertIn("email", resp.context["form"].errors)
        # No second account and no token/e-mail side effects.
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(EmailConfirmation.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_mixed_case_variant_form_is_invalid(self):
        form = SignUpForm(
            data=_valid_payload(username="impostor", email="Rt_User@Example.Com")
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)


class SignupNormalisesEmailToLowercaseTests(TestCase):
    """#13 — a fresh signup stores its e-mail lowercased."""

    def test_signup_lowercases_stored_email(self):
        resp = self.client.post(
            reverse("signup"),
            _valid_payload(username="newcomer", email="MixedCase@Example.COM"),
        )
        self.assertEqual(resp.status_code, 200)
        user = User.objects.get(username="newcomer")
        self.assertEqual(user.email, "mixedcase@example.com")

    def test_form_clean_returns_lowercased_email(self):
        form = SignUpForm(
            data=_valid_payload(username="newcomer", email="MixedCase@Example.COM")
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["email"], "mixedcase@example.com")


class ResendConfirmationOpenRedirectTests(TestCase):
    """#30/#38 — resend_confirmation must not redirect to an external host."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="unconfirmed",
            email="unconfirmed@example.com",
            password=STRONG_PASSWORD,
            email_confirmed=False,
        )
        self.client.force_login(self.user)

    def test_external_referer_falls_back_to_forum_index(self):
        resp = self.client.post(
            reverse("resend_confirmation"),
            HTTP_REFERER="https://evil.example.com/phish",
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], reverse("forum:index"))

    def test_scheme_relative_external_referer_falls_back_to_forum_index(self):
        # //evil.example.com is a host-changing redirect too.
        resp = self.client.post(
            reverse("resend_confirmation"),
            HTTP_REFERER="//evil.example.com/phish",
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], reverse("forum:index"))

    def test_same_host_relative_referer_is_still_honoured(self):
        # A safe on-site referer continues to work (no regression).
        referer = "/forum/post/1/"
        resp = self.client.post(
            reverse("resend_confirmation"), HTTP_REFERER=referer
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], referer)
