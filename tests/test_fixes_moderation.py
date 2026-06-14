"""Regression tests for the confirmed moderation bug fixes.

One area per fix; each test asserts the *corrected* behavior and would fail on
the old behavior:

  * #9  — a non-Creator moderator cannot LIFT a Creator-issued ban of a
          Community Moderator (the ban must stay active); a Creator can.
  * #22 — a non-Creator moderator cannot SILENCE a peer Moderator (same
          authority as a ban).
  * #21/#26/#39 — move_post with a non-integer / empty ``subtopic`` returns a
          clean 404 (never a 500).
  * #34 — ban() is idempotent: re-banning yields exactly one active Ban.
  * #35/#38 — _back() never honours an external Referer (open-redirect guard).

Mirrors tests/test_moderation_actions.py: confirmed/active users via role
flags, SiteConfiguration thresholds set in setUp, services called directly for
unit-style assertions plus the Client for the view-level (move_post / open
redirect) checks.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from accounts.models import Level
from core.models import SiteConfiguration
from forum.models import Post, Subtopic, Topic
from moderation import services as mod
from moderation.models import Ban, Silence

User = get_user_model()


def make_user(username, *, email=None, **flags):
    """Create a confirmed, active user with optional role flags."""
    return User.objects.create_user(
        username=username,
        email=email or f"{username}@example.com",
        password="pw-12345",
        email_confirmed=True,
        **flags,
    )


class ModerationFixBase(TestCase):
    def setUp(self):
        # Thresholds set so Collaborator/Founder derivation never raises.
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()

        self.moderator = make_user("mod", is_community_moderator=True)
        self.other_mod = make_user("mod2", is_community_moderator=True)
        self.creator = make_user("creator", is_riffhub_creator=True)
        self.regular = make_user("regular")
        self.victim = make_user("victim")

        self.topic = Topic.objects.create(name="Gear", is_predefined=True)
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")
        self.other_subtopic = Subtopic.objects.create(
            topic=self.topic, name="Basses"
        )

    def fresh(self, user):
        return User.objects.get(pk=user.pk)


# ---------------------------------------------------------------------------
# #9 — lifting a sanction needs the same authority as imposing it
# ---------------------------------------------------------------------------
class LiftBanAuthorityTests(ModerationFixBase):
    def test_non_creator_moderator_cannot_lift_ban_of_a_moderator(self):
        # Creator bans a moderator (legitimate), then a peer moderator tries to lift.
        ban = mod.ban(self.creator, self.other_mod, reason="abuse of tools")
        with self.assertRaises(PermissionDenied):
            mod.lift_ban(self.moderator, self.other_mod)
        ban.refresh_from_db()
        self.assertIsNone(ban.lifted_at)  # ban stays active
        self.assertFalse(self.fresh(self.other_mod).is_active)

    def test_creator_can_lift_ban_of_a_moderator(self):
        mod.ban(self.creator, self.other_mod, reason="abuse of tools")
        mod.lift_ban(self.creator, self.other_mod)
        self.assertFalse(
            Ban.objects.filter(
                target=self.other_mod, lifted_at__isnull=True
            ).exists()
        )
        self.assertTrue(self.fresh(self.other_mod).is_active)

    def test_moderator_can_still_lift_ban_of_a_regular_user(self):
        # The authority guard must not over-block ordinary lifts.
        mod.ban(self.moderator, self.victim, reason="illegal content")
        mod.lift_ban(self.moderator, self.victim)
        self.assertTrue(self.fresh(self.victim).is_active)

    def test_lift_ban_view_does_not_lift_a_moderators_ban_for_non_creator(self):
        # The view catches the service's PermissionDenied and redirects back
        # with an error message; crucially the ban is NOT lifted.
        mod.ban(self.creator, self.other_mod, reason="abuse of tools")
        self.client.force_login(self.moderator)
        resp = self.client.post(
            reverse("moderation:lift_ban_user", args=[self.other_mod.pk])
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            Ban.objects.filter(
                target=self.other_mod, lifted_at__isnull=True
            ).exists()
        )
        self.assertFalse(self.fresh(self.other_mod).is_active)


# ---------------------------------------------------------------------------
# #22 — silence is a near-equivalent sanction; same authority guard
# ---------------------------------------------------------------------------
class SilenceAuthorityTests(ModerationFixBase):
    def test_non_creator_moderator_cannot_silence_a_peer_moderator(self):
        with self.assertRaises(PermissionDenied):
            mod.silence(self.moderator, self.other_mod, reason="threats")
        self.assertEqual(Silence.objects.count(), 0)

    def test_creator_can_silence_a_moderator(self):
        silence = mod.silence(self.creator, self.other_mod, reason="threats")
        self.assertEqual(silence.target, self.other_mod)
        self.assertEqual(silence.sequence, 1)

    def test_moderator_can_still_silence_a_regular_user(self):
        silence = mod.silence(self.moderator, self.victim, reason="threats")
        self.assertEqual(silence.target, self.victim)


# ---------------------------------------------------------------------------
# #21/#26/#39 — move_post must never 500 on a bad subtopic value
# ---------------------------------------------------------------------------
class MovePostBadSubtopicTests(ModerationFixBase):
    def setUp(self):
        super().setUp()
        self.post = Post.objects.create(
            subtopic=self.subtopic,
            author=self.regular,
            title="My rig",
            body="posted under guitars",
        )
        self.url = reverse("moderation:move_post", args=[self.post.pk])

    def test_non_integer_subtopic_returns_404_not_500(self):
        self.client.force_login(self.moderator)
        resp = self.client.post(self.url, {"subtopic": "abc"})
        self.assertEqual(resp.status_code, 404)
        self.post.refresh_from_db()
        self.assertEqual(self.post.subtopic_id, self.subtopic.pk)  # unchanged

    def test_empty_subtopic_returns_404_not_500(self):
        self.client.force_login(self.moderator)
        resp = self.client.post(self.url, {"subtopic": ""})
        self.assertEqual(resp.status_code, 404)
        self.post.refresh_from_db()
        self.assertEqual(self.post.subtopic_id, self.subtopic.pk)

    def test_missing_subtopic_returns_404_not_500(self):
        self.client.force_login(self.moderator)
        resp = self.client.post(self.url, {})
        self.assertEqual(resp.status_code, 404)

    def test_valid_subtopic_still_moves(self):
        self.client.force_login(self.moderator)
        resp = self.client.post(self.url, {"subtopic": self.other_subtopic.pk})
        self.assertEqual(resp.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.subtopic_id, self.other_subtopic.pk)


# ---------------------------------------------------------------------------
# #34 — ban() is idempotent
# ---------------------------------------------------------------------------
class BanIdempotencyTests(ModerationFixBase):
    def test_re_banning_yields_exactly_one_active_ban(self):
        first = mod.ban(self.moderator, self.victim, reason="illegal content")
        second = mod.ban(self.moderator, self.victim, reason="illegal content")
        self.assertEqual(
            Ban.objects.filter(
                target=self.victim, lifted_at__isnull=True
            ).count(),
            1,
        )
        self.assertEqual(first.pk, second.pk)  # the existing ban is returned
        self.assertFalse(self.fresh(self.victim).is_active)

    def test_ban_again_after_lift_creates_a_fresh_ban(self):
        mod.ban(self.moderator, self.victim, reason="illegal content")
        mod.lift_ban(self.moderator, self.victim)
        mod.ban(self.moderator, self.victim, reason="re-offended")
        self.assertEqual(
            Ban.objects.filter(
                target=self.victim, lifted_at__isnull=True
            ).count(),
            1,
        )
        self.assertEqual(Ban.objects.filter(target=self.victim).count(), 2)


# ---------------------------------------------------------------------------
# #35/#38 — _back() must not honour an external Referer (open redirect)
# ---------------------------------------------------------------------------
class BackOpenRedirectTests(ModerationFixBase):
    def test_external_referer_redirects_to_dashboard(self):
        self.client.force_login(self.moderator)
        resp = self.client.post(
            reverse("moderation:silence_user", args=[self.victim.pk]),
            {"reason": "threats"},
            HTTP_REFERER="https://evil.example.com/phish",
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("moderation:dashboard"))

    def test_no_referer_redirects_to_dashboard(self):
        self.client.force_login(self.moderator)
        resp = self.client.post(
            reverse("moderation:warn_user", args=[self.victim.pk]),
            {"reason": "heads up"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("moderation:dashboard"))

    def test_same_host_referer_is_preserved(self):
        self.client.force_login(self.moderator)
        target = f"/u/{self.victim.username}/"
        resp = self.client.post(
            reverse("moderation:warn_user", args=[self.victim.pk]),
            {"reason": "heads up"},
            HTTP_REFERER=f"http://testserver{target}",
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], f"http://testserver{target}")
