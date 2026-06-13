"""Tests for the self-serve sign-up flow.

Covers ``accounts.views.signup`` (GET/POST at ``/accounts/signup/``),
``accounts.forms.SignUpForm`` validation, and the side effects of a successful
sign-up: the user is created, logged in, ``email_confirmed`` starts ``False``,
and a *pending* ``EmailConfirmation`` token is created.

PRODUCT.md rules exercised here:
- Anyone can sign up (anonymous GET renders the form).
- Confirming e-mail is what unlocks collab-db contributions, so a fresh sign-up
  must NOT be confirmed yet — ``email_confirmed`` is ``False`` and the created
  ``EmailConfirmation`` token is still pending (``confirmed_at is None``).
- E-mail is unique on the custom user model; duplicate username/e-mail and
  weak/mismatched passwords are rejected and create no user.
"""

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from accounts.forms import SignUpForm
from accounts.models import EmailConfirmation

User = get_user_model()


# A password that comfortably clears every configured validator
# (UserAttributeSimilarity / MinimumLength / CommonPassword / NumericPassword).
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


class SignUpGetTests(TestCase):
    """GET /accounts/signup/ — the registration page itself."""

    def test_signup_url_resolves(self):
        self.assertEqual(reverse("signup"), "/accounts/signup/")

    def test_get_renders_form_for_anonymous(self):
        resp = self.client.get(reverse("signup"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "registration/signup.html")

    def test_get_provides_signup_form_in_context(self):
        resp = self.client.get(reverse("signup"))
        self.assertIn("form", resp.context)
        self.assertIsInstance(resp.context["form"], SignUpForm)
        # The form is unbound on GET (no submitted data yet).
        self.assertFalse(resp.context["form"].is_bound)

    def test_get_does_not_create_any_user(self):
        self.client.get(reverse("signup"))
        self.assertEqual(User.objects.count(), 0)


class SignUpValidPostTests(TestCase):
    """POST /accounts/signup/ with a valid payload — the happy path."""

    def setUp(self):
        self.url = reverse("signup")
        self.resp = self.client.post(self.url, _valid_payload())

    def test_user_is_created(self):
        self.assertEqual(User.objects.count(), 1)
        user = User.objects.get()
        self.assertEqual(user.username, "stratlover")
        self.assertEqual(user.email, "strat@riffhub.test")

    def test_password_is_hashed_and_usable(self):
        user = User.objects.get(username="stratlover")
        # Stored hashed, never in plaintext, but verifies against the raw value.
        self.assertNotEqual(user.password, STRONG_PASSWORD)
        self.assertTrue(user.check_password(STRONG_PASSWORD))

    def test_email_confirmed_starts_false(self):
        user = User.objects.get(username="stratlover")
        self.assertFalse(user.email_confirmed)

    def test_user_starts_as_regular_with_no_roles(self):
        # A brand-new account has no granted roles and zero accepted submissions.
        user = User.objects.get(username="stratlover")
        self.assertFalse(user.is_founder)
        self.assertFalse(user.is_community_moderator)
        self.assertFalse(user.is_riffhub_creator)
        self.assertEqual(user.accepted_submissions_count, 0)

    def test_a_pending_email_confirmation_is_created(self):
        user = User.objects.get(username="stratlover")
        confirmations = EmailConfirmation.objects.filter(user=user)
        self.assertEqual(confirmations.count(), 1)
        confirmation = confirmations.get()
        # The token is pending: it has NOT been confirmed yet.
        self.assertIsNone(confirmation.confirmed_at)
        self.assertIsNotNone(confirmation.token)

    def test_user_is_logged_in_after_signup(self):
        # The session now identifies the freshly created user.
        user = User.objects.get(username="stratlover")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        # And a follow-up request is authenticated as that user.
        whoami = self.client.get(reverse("signup"))
        self.assertTrue(whoami.wsgi_request.user.is_authenticated)
        self.assertEqual(whoami.wsgi_request.user.pk, user.pk)

    def test_renders_signup_done_page(self):
        self.assertEqual(self.resp.status_code, 200)
        self.assertTemplateUsed(self.resp, "registration/signup_done.html")
        self.assertEqual(self.resp.context["email"], "strat@riffhub.test")

    def test_confirmation_email_is_sent(self):
        # _send_confirmation calls send_mail; the locmem backend captures it.
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["strat@riffhub.test"])
        self.assertIn("Confirm", sent.subject)

    def test_confirmation_link_in_email_targets_the_pending_token(self):
        user = User.objects.get(username="stratlover")
        token = EmailConfirmation.objects.get(user=user).token
        self.assertIn(str(token), mail.outbox[0].body)


class SignUpDebugLinkTests(TestCase):
    """The dev-only confirmation link is only surfaced when DEBUG is on."""

    def test_confirmation_link_hidden_when_debug_false(self):
        with self.settings(DEBUG=False):
            resp = self.client.post(reverse("signup"), _valid_payload())
        self.assertIsNone(resp.context["confirmation_link"])

    def test_confirmation_link_shown_when_debug_true(self):
        with self.settings(DEBUG=True):
            resp = self.client.post(reverse("signup"), _valid_payload())
        link = resp.context["confirmation_link"]
        self.assertIsNotNone(link)
        # It points at the confirm_email view for the created token.
        token = EmailConfirmation.objects.get().token
        self.assertIn(reverse("confirm_email", args=[token]), link)


class SignUpPasswordRejectionTests(TestCase):
    """Weak / mismatched passwords are rejected and create no user."""

    def setUp(self):
        self.url = reverse("signup")

    def _assert_no_user_and_form_errors(self, resp):
        # Re-renders the form (200, not a redirect) with errors and no new user.
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "registration/signup.html")
        self.assertFalse(resp.context["form"].is_valid())
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(EmailConfirmation.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_mismatched_passwords_rejected(self):
        resp = self.client.post(
            self.url,
            _valid_payload(password2="A-Totally-Different-Pw-99!"),
        )
        self._assert_no_user_and_form_errors(resp)
        self.assertIn("password2", resp.context["form"].errors)

    def test_too_short_password_rejected(self):
        # Below MinimumLengthValidator's 8-char floor.
        resp = self.client.post(
            self.url, _valid_payload(password1="Ab1!", password2="Ab1!")
        )
        self._assert_no_user_and_form_errors(resp)
        self.assertIn("password2", resp.context["form"].errors)

    def test_common_password_rejected(self):
        # "password" is in the CommonPasswordValidator blocklist.
        resp = self.client.post(
            self.url, _valid_payload(password1="password", password2="password")
        )
        self._assert_no_user_and_form_errors(resp)

    def test_entirely_numeric_password_rejected(self):
        # NumericPasswordValidator rejects all-digit passwords.
        resp = self.client.post(
            self.url, _valid_payload(password1="89345172", password2="89345172")
        )
        self._assert_no_user_and_form_errors(resp)

    def test_password_too_similar_to_username_rejected(self):
        # UserAttributeSimilarityValidator: password ~ username.
        resp = self.client.post(
            self.url,
            _valid_payload(
                username="walnutamber",
                password1="walnutamber1",
                password2="walnutamber1",
            ),
        )
        self._assert_no_user_and_form_errors(resp)

    def test_blank_passwords_rejected(self):
        resp = self.client.post(
            self.url, _valid_payload(password1="", password2="")
        )
        self._assert_no_user_and_form_errors(resp)


class SignUpDuplicateRejectionTests(TestCase):
    """Duplicate username / e-mail are rejected; no second user is created."""

    def setUp(self):
        self.url = reverse("signup")
        self.existing = User.objects.create_user(
            username="stratlover",
            email="strat@riffhub.test",
            password=STRONG_PASSWORD,
        )

    def test_duplicate_username_rejected(self):
        resp = self.client.post(
            self.url,
            _valid_payload(username="stratlover", email="new@riffhub.test"),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["form"].is_valid())
        self.assertIn("username", resp.context["form"].errors)
        # Still exactly the one pre-existing user.
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(EmailConfirmation.objects.count(), 0)

    def test_duplicate_email_rejected(self):
        # ``email`` is unique=True on the custom user model and is listed in the
        # form's Meta.fields, so the ModelForm's uniqueness check must flag it.
        resp = self.client.post(
            self.url,
            _valid_payload(username="lespaullover", email="strat@riffhub.test"),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["form"].is_valid())
        self.assertIn("email", resp.context["form"].errors)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(EmailConfirmation.objects.count(), 0)

    def test_duplicate_email_rejected_case_insensitive_domain(self):
        # Postgres EmailField uniqueness is exact on the stored value; signing
        # up with the identical address must be rejected regardless of case in
        # the (case-insensitive) domain part being normalised the same way.
        resp = self.client.post(
            self.url,
            _valid_payload(username="another", email="strat@riffhub.test"),
        )
        self.assertFalse(resp.context["form"].is_valid())
        self.assertEqual(User.objects.count(), 1)


class SignUpMissingFieldsTests(TestCase):
    """Required fields (username, email) must be present."""

    def setUp(self):
        self.url = reverse("signup")

    def test_missing_email_rejected(self):
        resp = self.client.post(self.url, _valid_payload(email=""))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["form"].is_valid())
        self.assertIn("email", resp.context["form"].errors)
        self.assertEqual(User.objects.count(), 0)

    def test_missing_username_rejected(self):
        resp = self.client.post(self.url, _valid_payload(username=""))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["form"].is_valid())
        self.assertIn("username", resp.context["form"].errors)
        self.assertEqual(User.objects.count(), 0)

    def test_malformed_email_rejected(self):
        resp = self.client.post(self.url, _valid_payload(email="not-an-email"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["form"].is_valid())
        self.assertIn("email", resp.context["form"].errors)
        self.assertEqual(User.objects.count(), 0)


class SignUpAuthenticatedRedirectTests(TestCase):
    """An already-authenticated user visiting signup is redirected away."""

    def setUp(self):
        self.url = reverse("signup")
        self.user = User.objects.create_user(
            username="already_in",
            email="in@riffhub.test",
            password=STRONG_PASSWORD,
        )

    def test_authenticated_get_redirects_to_forum_index(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("forum:index"))

    def test_authenticated_post_redirects_without_creating_user(self):
        # Even a POST is short-circuited by the redirect — no second account.
        self.client.force_login(self.user)
        resp = self.client.post(
            self.url,
            _valid_payload(username="sneaky", email="sneaky@riffhub.test"),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("forum:index"))
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(User.objects.filter(username="sneaky").exists())
        self.assertEqual(len(mail.outbox), 0)


class SignUpFormUnitTests(TestCase):
    """Direct unit tests on SignUpForm (independent of the view)."""

    def test_form_declares_username_and_email_fields(self):
        form = SignUpForm()
        # password1/password2 come from UserCreationForm; username/email are ours.
        self.assertIn("username", form.fields)
        self.assertIn("email", form.fields)
        self.assertIn("password1", form.fields)
        self.assertIn("password2", form.fields)

    def test_email_is_required_on_the_form(self):
        self.assertTrue(SignUpForm().fields["email"].required)

    def test_valid_form_saves_user_with_email(self):
        form = SignUpForm(data=_valid_payload())
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.email, "strat@riffhub.test")
        self.assertTrue(user.check_password(STRONG_PASSWORD))
        # The form alone does NOT create an EmailConfirmation (the view does).
        self.assertEqual(EmailConfirmation.objects.filter(user=user).count(), 0)

    def test_saved_user_is_not_confirmed_by_default(self):
        form = SignUpForm(data=_valid_payload())
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertFalse(user.email_confirmed)
