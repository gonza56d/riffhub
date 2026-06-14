"""Tests for the light/dark theme preference (persistence + resolution).

Backend scope only: the User.theme field, the set_theme endpoint (cookie for
everyone + account for logged-in users), and the context processor that resolves
the active theme onto <html data-theme="...">.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class ThemePersistenceTests(TestCase):
    def setUp(self):
        self.url = reverse("set_theme")

    def test_default_theme_is_light(self):
        user = User.objects.create_user(username="riff", email="r@x.com", password="pw")
        self.assertEqual(user.theme, "light")

    def test_anonymous_toggle_sets_cookie_only(self):
        resp = self.client.post(self.url, {"theme": "dark"})
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(resp.cookies["theme"].value, "dark")

    def test_authenticated_toggle_persists_to_account(self):
        user = User.objects.create_user(username="riff", email="r@x.com", password="pw")
        self.client.force_login(user)
        resp = self.client.post(self.url, {"theme": "dark"})
        self.assertEqual(resp.status_code, 204)
        user.refresh_from_db()
        self.assertEqual(user.theme, "dark")
        self.assertEqual(resp.cookies["theme"].value, "dark")

    def test_invalid_theme_rejected(self):
        self.assertEqual(self.client.post(self.url, {"theme": "neon"}).status_code, 400)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_next_redirect_for_no_js_fallback(self):
        resp = self.client.post(self.url, {"theme": "dark", "next": "/"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/")
        self.assertEqual(resp.cookies["theme"].value, "dark")

    def test_open_redirect_is_blocked(self):
        # An off-site next is ignored — we fall back to the empty 204 response.
        resp = self.client.post(self.url, {"theme": "dark", "next": "https://evil.test/x"})
        self.assertEqual(resp.status_code, 204)


class ThemeResolutionTests(TestCase):
    """The context processor renders the active theme onto the page shell."""

    def test_anonymous_defaults_to_light(self):
        self.assertContains(self.client.get("/"), 'data-theme="light"')

    def test_anonymous_cookie_drives_theme(self):
        self.client.cookies["theme"] = "dark"
        self.assertContains(self.client.get("/"), 'data-theme="dark"')

    def test_authenticated_account_preference_wins(self):
        user = User.objects.create_user(
            username="riff", email="r@x.com", password="pw", theme="dark"
        )
        self.client.force_login(user)
        self.assertContains(self.client.get("/"), 'data-theme="dark"')
