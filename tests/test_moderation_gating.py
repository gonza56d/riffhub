"""Tests for moderation endpoint gating + the ``moderation_flags`` context processor.

PRODUCT.md: the moderation tools target *unrelated/illegal* content and threats,
and they belong to Community Moderators and Riffhub Creators only ("All the power
that a Riffhub Community Founder has, plus they are able to delete other users'
comments, posts, warn them, and ultimately ban them"). Everyone below that level
— including the Database Collaborator and the sticky Community Founder — must be
locked out.

This module checks two things end to end:

1. **Endpoint gating.** Every moderation endpoint (the ``/moderation/`` dashboard
   plus the POST actions warn / silence / ban / lift-ban / move / remove / restore)
   requires Moderator level or higher. A regular (logged-in) user and an anonymous
   visitor both get HTTP 403 *and the action has no side effect* (no audit row is
   created, the target's account / content is untouched). Positive controls confirm
   a real moderator passes the same gate, proving the level check — not some
   unrelated error — is what blocks the others.

2. **The ``moderation.context_processors.moderation_flags`` processor** exposes the
   right ``is_moderator`` / ``is_creator`` booleans for anonymous / regular /
   moderator / creator users (and that a Creator implies Moderator), both as a unit
   and through the rendered navigation in ``templates/base.html``.

All gating runs through ``moderation.views._require_moderator`` and
``moderation.services._require_moderator`` which raise ``PermissionDenied`` (→ 403).
The action views are ``@require_POST``-wrapped, so a GET hits the 405 method gate
*before* the permission gate; the gating cases therefore POST.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import Level
from core.models import SiteConfiguration
from forum.models import Comment, Post, Subtopic, Topic
from moderation.context_processors import moderation_flags
from moderation.models import Ban, ContentAction, Silence, Warning

User = get_user_model()


# --------------------------------------------------------------------------- #
# Shared fixture helpers                                                       #
# --------------------------------------------------------------------------- #
class _ModerationFixtureMixin:
    """Builds the minimal cast of users + a post/comment to act on.

    The collaborator-promotion threshold is set so the *derived* levels behave
    deterministically (otherwise reading ``user.level`` for a collaborator/founder
    would fall back to Regular when the threshold is unset).
    """

    def _config(self):
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()
        return config

    def _make_user(self, username, **flags):
        defaults = dict(
            email=f"{username}@riffhub.test",
            password="pw-very-secret-123",
            email_confirmed=True,
        )
        defaults.update(flags)
        return User.objects.create_user(username=username, **defaults)

    def setUp(self):
        super().setUp()
        self._config()

        # The full ladder of identities relevant to "Moderator+".
        self.anon = AnonymousUser()
        self.regular = self._make_user("regular")
        self.collaborator = self._make_user("collab", accepted_submissions_count=3)
        self.founder = self._make_user("founder", is_founder=True)
        self.moderator = self._make_user("mod", is_community_moderator=True)
        self.creator = self._make_user("creator", is_riffhub_creator=True)

        # A target to be warned / silenced / banned, and content to move/remove.
        self.target = self._make_user("victim")

        self.topic = Topic.objects.create(name="Gear")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")
        self.other_subtopic = Subtopic.objects.create(topic=self.topic, name="Basses")
        self.post = Post.objects.create(
            subtopic=self.subtopic,
            author=self.target,
            title="My rig",
            body="Look at my guitar.",
        )
        self.comment = Comment.objects.create(
            post=self.post, author=self.target, body="A reply."
        )


# --------------------------------------------------------------------------- #
# Level sanity check — confirms the fixture identities resolve as intended     #
# --------------------------------------------------------------------------- #
class FixtureLevelSanityTests(_ModerationFixtureMixin, TestCase):
    """Guard the fixtures: the gating tests are only meaningful if these hold."""

    def test_regular_is_below_moderator(self):
        self.assertEqual(self.regular.level, Level.REGULAR)
        self.assertFalse(self.regular.is_at_least(Level.MODERATOR))

    def test_collaborator_is_below_moderator(self):
        self.assertEqual(self.collaborator.level, Level.COLLABORATOR)
        self.assertFalse(self.collaborator.is_at_least(Level.MODERATOR))

    def test_founder_is_below_moderator(self):
        self.assertEqual(self.founder.level, Level.FOUNDER)
        self.assertFalse(self.founder.is_at_least(Level.MODERATOR))

    def test_moderator_meets_moderator(self):
        self.assertEqual(self.moderator.level, Level.MODERATOR)
        self.assertTrue(self.moderator.is_at_least(Level.MODERATOR))

    def test_creator_meets_moderator_and_creator(self):
        self.assertEqual(self.creator.level, Level.CREATOR)
        self.assertTrue(self.creator.is_at_least(Level.MODERATOR))
        self.assertTrue(self.creator.is_at_least(Level.CREATOR))


# --------------------------------------------------------------------------- #
# Dashboard (GET) gating                                                       #
# --------------------------------------------------------------------------- #
class DashboardGatingTests(_ModerationFixtureMixin, TestCase):
    """The ``/moderation/`` dashboard is a plain GET, gated to Moderator+."""

    def setUp(self):
        super().setUp()
        self.url = reverse("moderation:dashboard")

    def test_url_resolves_to_expected_path(self):
        self.assertEqual(self.url, "/moderation/")

    def test_anonymous_gets_403(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_regular_user_gets_403(self):
        self.client.force_login(self.regular)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_collaborator_gets_403(self):
        self.client.force_login(self.collaborator)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_founder_gets_403(self):
        # A sticky Founder has Founder power but is NOT a moderator (PRODUCT.md).
        self.client.force_login(self.founder)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_moderator_gets_200(self):
        self.client.force_login(self.moderator)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "moderation/dashboard.html")

    def test_creator_gets_200(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)


# --------------------------------------------------------------------------- #
# Warn endpoint gating                                                         #
# --------------------------------------------------------------------------- #
class WarnGatingTests(_ModerationFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("moderation:warn_user", args=[self.target.pk])

    def _post(self):
        return self.client.post(self.url, {"reason": "spamming"})

    def test_anonymous_is_forbidden_and_creates_no_warning(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Warning.objects.count(), 0)

    def test_regular_user_is_forbidden_and_creates_no_warning(self):
        self.client.force_login(self.regular)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Warning.objects.count(), 0)

    def test_collaborator_is_forbidden_and_creates_no_warning(self):
        self.client.force_login(self.collaborator)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Warning.objects.count(), 0)

    def test_founder_is_forbidden_and_creates_no_warning(self):
        self.client.force_login(self.founder)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Warning.objects.count(), 0)

    def test_get_request_is_method_not_allowed(self):
        # POST-only endpoint: a GET trips the 405 method gate (no warning either).
        self.client.force_login(self.regular)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(Warning.objects.count(), 0)

    def test_moderator_can_warn(self):
        # Positive control: the only difference from the blocked cases is the level.
        self.client.force_login(self.moderator)
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Warning.objects.filter(target=self.target).count(), 1)


# --------------------------------------------------------------------------- #
# Silence endpoint gating                                                      #
# --------------------------------------------------------------------------- #
class SilenceGatingTests(_ModerationFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("moderation:silence_user", args=[self.target.pk])

    def _post(self):
        return self.client.post(self.url, {"reason": "threats"})

    def test_anonymous_is_forbidden_and_creates_no_silence(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Silence.objects.count(), 0)

    def test_regular_user_is_forbidden_and_creates_no_silence(self):
        self.client.force_login(self.regular)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Silence.objects.count(), 0)

    def test_collaborator_is_forbidden_and_creates_no_silence(self):
        self.client.force_login(self.collaborator)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Silence.objects.count(), 0)

    def test_founder_is_forbidden_and_creates_no_silence(self):
        self.client.force_login(self.founder)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Silence.objects.count(), 0)

    def test_get_request_is_method_not_allowed(self):
        self.client.force_login(self.regular)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(Silence.objects.count(), 0)

    def test_moderator_can_silence(self):
        self.client.force_login(self.moderator)
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Silence.objects.filter(target=self.target).count(), 1)


# --------------------------------------------------------------------------- #
# Ban endpoint gating                                                          #
# --------------------------------------------------------------------------- #
class BanGatingTests(_ModerationFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("moderation:ban_user", args=[self.target.pk])

    def _post(self):
        return self.client.post(self.url, {"reason": "illegal content"})

    def _assert_no_effect(self):
        self.assertEqual(Ban.objects.count(), 0)
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)  # never deactivated

    def test_anonymous_is_forbidden_and_does_not_ban(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self._assert_no_effect()

    def test_regular_user_is_forbidden_and_does_not_ban(self):
        self.client.force_login(self.regular)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self._assert_no_effect()

    def test_collaborator_is_forbidden_and_does_not_ban(self):
        self.client.force_login(self.collaborator)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self._assert_no_effect()

    def test_founder_is_forbidden_and_does_not_ban(self):
        self.client.force_login(self.founder)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self._assert_no_effect()

    def test_get_request_is_method_not_allowed(self):
        self.client.force_login(self.regular)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)
        self._assert_no_effect()

    def test_moderator_can_ban(self):
        self.client.force_login(self.moderator)
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Ban.objects.filter(target=self.target).count(), 1)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)


# --------------------------------------------------------------------------- #
# Lift-ban endpoint gating                                                     #
# --------------------------------------------------------------------------- #
class LiftBanGatingTests(_ModerationFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        # Pre-existing active ban that the gated callers must NOT be able to lift.
        self.ban = Ban.objects.create(
            target=self.target, issued_by=self.moderator, reason="illegal"
        )
        self.target.is_active = False
        self.target.save(update_fields=["is_active"])
        self.url = reverse("moderation:lift_ban_user", args=[self.target.pk])

    def _post(self):
        return self.client.post(self.url)

    def _assert_ban_still_active(self):
        self.ban.refresh_from_db()
        self.assertIsNone(self.ban.lifted_at)
        self.target.refresh_from_db()
        self.assertFalse(self.target.is_active)  # still deactivated

    def test_anonymous_is_forbidden_and_ban_persists(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self._assert_ban_still_active()

    def test_regular_user_is_forbidden_and_ban_persists(self):
        self.client.force_login(self.regular)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self._assert_ban_still_active()

    def test_collaborator_is_forbidden_and_ban_persists(self):
        self.client.force_login(self.collaborator)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self._assert_ban_still_active()

    def test_founder_is_forbidden_and_ban_persists(self):
        self.client.force_login(self.founder)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self._assert_ban_still_active()

    def test_get_request_is_method_not_allowed(self):
        self.client.force_login(self.regular)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)
        self._assert_ban_still_active()

    def test_moderator_can_lift_ban(self):
        self.client.force_login(self.moderator)
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        self.ban.refresh_from_db()
        self.assertIsNotNone(self.ban.lifted_at)
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)


# --------------------------------------------------------------------------- #
# Move-post endpoint gating                                                    #
# --------------------------------------------------------------------------- #
class MovePostGatingTests(_ModerationFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("moderation:move_post", args=[self.post.pk])

    def _post(self):
        return self.client.post(self.url, {"subtopic": self.other_subtopic.pk})

    def _assert_not_moved(self):
        self.post.refresh_from_db()
        self.assertEqual(self.post.subtopic_id, self.subtopic.pk)  # unchanged
        self.assertEqual(ContentAction.objects.count(), 0)

    def test_anonymous_is_forbidden_and_post_not_moved(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self._assert_not_moved()

    def test_regular_user_is_forbidden_and_post_not_moved(self):
        self.client.force_login(self.regular)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self._assert_not_moved()

    def test_collaborator_is_forbidden_and_post_not_moved(self):
        self.client.force_login(self.collaborator)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self._assert_not_moved()

    def test_founder_is_forbidden_and_post_not_moved(self):
        self.client.force_login(self.founder)
        resp = self._post()
        self.assertEqual(resp.status_code, 403)
        self._assert_not_moved()

    def test_get_request_is_method_not_allowed(self):
        self.client.force_login(self.regular)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)
        self._assert_not_moved()

    def test_moderator_can_move_post(self):
        self.client.force_login(self.moderator)
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.subtopic_id, self.other_subtopic.pk)
        self.assertEqual(
            ContentAction.objects.filter(action="move").count(), 1
        )


# --------------------------------------------------------------------------- #
# Remove-content endpoint gating (post & comment kinds)                        #
# --------------------------------------------------------------------------- #
class RemoveContentGatingTests(_ModerationFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.post_url = reverse(
            "moderation:remove_content", args=["post", self.post.pk]
        )
        self.comment_url = reverse(
            "moderation:remove_content", args=["comment", self.comment.pk]
        )

    def _assert_nothing_removed(self):
        self.post.refresh_from_db()
        self.comment.refresh_from_db()
        self.assertFalse(self.post.is_removed)
        self.assertFalse(self.comment.is_removed)
        self.assertEqual(ContentAction.objects.count(), 0)

    def test_anonymous_cannot_remove_post(self):
        resp = self.client.post(self.post_url, {"reason": "off-topic"})
        self.assertEqual(resp.status_code, 403)
        self._assert_nothing_removed()

    def test_anonymous_cannot_remove_comment(self):
        resp = self.client.post(self.comment_url, {"reason": "off-topic"})
        self.assertEqual(resp.status_code, 403)
        self._assert_nothing_removed()

    def test_regular_user_cannot_remove_post(self):
        self.client.force_login(self.regular)
        resp = self.client.post(self.post_url, {"reason": "off-topic"})
        self.assertEqual(resp.status_code, 403)
        self._assert_nothing_removed()

    def test_regular_user_cannot_remove_comment(self):
        self.client.force_login(self.regular)
        resp = self.client.post(self.comment_url, {"reason": "off-topic"})
        self.assertEqual(resp.status_code, 403)
        self._assert_nothing_removed()

    def test_collaborator_cannot_remove_post(self):
        self.client.force_login(self.collaborator)
        resp = self.client.post(self.post_url, {"reason": "off-topic"})
        self.assertEqual(resp.status_code, 403)
        self._assert_nothing_removed()

    def test_founder_cannot_remove_post(self):
        self.client.force_login(self.founder)
        resp = self.client.post(self.post_url, {"reason": "off-topic"})
        self.assertEqual(resp.status_code, 403)
        self._assert_nothing_removed()

    def test_get_request_is_method_not_allowed(self):
        self.client.force_login(self.regular)
        resp = self.client.get(self.post_url)
        self.assertEqual(resp.status_code, 405)
        self._assert_nothing_removed()

    def test_moderator_can_remove_post(self):
        self.client.force_login(self.moderator)
        resp = self.client.post(self.post_url, {"reason": "off-topic"})
        self.assertEqual(resp.status_code, 302)
        self.post.refresh_from_db()
        self.assertTrue(self.post.is_removed)
        self.assertEqual(
            ContentAction.objects.filter(action="remove").count(), 1
        )

    def test_moderator_can_remove_comment(self):
        self.client.force_login(self.moderator)
        resp = self.client.post(self.comment_url, {"reason": "off-topic"})
        self.assertEqual(resp.status_code, 302)
        self.comment.refresh_from_db()
        self.assertTrue(self.comment.is_removed)

    def test_unknown_kind_is_404_for_moderator(self):
        # The kind dispatch (_content_obj) only knows post/comment; anything else
        # is a 404 even for a real moderator.
        self.client.force_login(self.moderator)
        url = reverse("moderation:remove_content", args=["widget", self.post.pk])
        resp = self.client.post(url, {"reason": "x"})
        self.assertEqual(resp.status_code, 404)


# --------------------------------------------------------------------------- #
# Restore-content endpoint gating (post & comment kinds)                       #
# --------------------------------------------------------------------------- #
class RestoreContentGatingTests(_ModerationFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        # Start with both already removed so a successful restore is observable.
        self.post.mark_removed(by=self.moderator, reason="off-topic")
        self.comment.mark_removed(by=self.moderator, reason="off-topic")
        self.post_url = reverse(
            "moderation:restore_content", args=["post", self.post.pk]
        )
        self.comment_url = reverse(
            "moderation:restore_content", args=["comment", self.comment.pk]
        )

    def _assert_still_removed(self):
        self.post.refresh_from_db()
        self.comment.refresh_from_db()
        self.assertTrue(self.post.is_removed)
        self.assertTrue(self.comment.is_removed)
        # No RESTORE audit row should be written by a blocked caller.
        self.assertFalse(
            ContentAction.objects.filter(action="restore").exists()
        )

    def test_anonymous_cannot_restore_post(self):
        resp = self.client.post(self.post_url)
        self.assertEqual(resp.status_code, 403)
        self._assert_still_removed()

    def test_anonymous_cannot_restore_comment(self):
        resp = self.client.post(self.comment_url)
        self.assertEqual(resp.status_code, 403)
        self._assert_still_removed()

    def test_regular_user_cannot_restore_post(self):
        self.client.force_login(self.regular)
        resp = self.client.post(self.post_url)
        self.assertEqual(resp.status_code, 403)
        self._assert_still_removed()

    def test_regular_user_cannot_restore_comment(self):
        self.client.force_login(self.regular)
        resp = self.client.post(self.comment_url)
        self.assertEqual(resp.status_code, 403)
        self._assert_still_removed()

    def test_collaborator_cannot_restore_post(self):
        self.client.force_login(self.collaborator)
        resp = self.client.post(self.post_url)
        self.assertEqual(resp.status_code, 403)
        self._assert_still_removed()

    def test_founder_cannot_restore_post(self):
        self.client.force_login(self.founder)
        resp = self.client.post(self.post_url)
        self.assertEqual(resp.status_code, 403)
        self._assert_still_removed()

    def test_get_request_is_method_not_allowed(self):
        self.client.force_login(self.regular)
        resp = self.client.get(self.post_url)
        self.assertEqual(resp.status_code, 405)
        self._assert_still_removed()

    def test_moderator_can_restore_post(self):
        # _back falls back to the dashboard when there is no Referer header.
        self.client.force_login(self.moderator)
        resp = self.client.post(self.post_url)
        self.assertEqual(resp.status_code, 302)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_removed)
        self.assertEqual(
            ContentAction.objects.filter(action="restore").count(), 1
        )

    def test_moderator_can_restore_comment(self):
        self.client.force_login(self.moderator)
        resp = self.client.post(self.comment_url)
        self.assertEqual(resp.status_code, 302)
        self.comment.refresh_from_db()
        self.assertFalse(self.comment.is_removed)


# --------------------------------------------------------------------------- #
# Cross-cutting: a creator passes every gate the moderator does                #
# --------------------------------------------------------------------------- #
class CreatorPassesEveryGateTests(_ModerationFixtureMixin, TestCase):
    """A Riffhub Creator outranks a Moderator, so every endpoint admits them."""

    def test_creator_reaches_dashboard(self):
        self.client.force_login(self.creator)
        resp = self.client.get(reverse("moderation:dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_creator_can_warn(self):
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("moderation:warn_user", args=[self.target.pk]),
            {"reason": "x"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Warning.objects.filter(target=self.target).count(), 1)

    def test_creator_can_move(self):
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("moderation:move_post", args=[self.post.pk]),
            {"subtopic": self.other_subtopic.pk},
        )
        self.assertEqual(resp.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.subtopic_id, self.other_subtopic.pk)


# --------------------------------------------------------------------------- #
# Enumerated sweep: no POST action endpoint admits anon / regular              #
# --------------------------------------------------------------------------- #
class AllPostEndpointsRejectNonModeratorsTests(_ModerationFixtureMixin, TestCase):
    """A compact sweep over *every* POST action endpoint at once.

    Complements the per-endpoint suites: if a new action endpoint is added that
    forgets the gate, this catches it as long as it is wired with these names.
    """

    def _endpoints(self):
        return [
            reverse("moderation:warn_user", args=[self.target.pk]),
            reverse("moderation:silence_user", args=[self.target.pk]),
            reverse("moderation:ban_user", args=[self.target.pk]),
            reverse("moderation:lift_ban_user", args=[self.target.pk]),
            reverse("moderation:move_post", args=[self.post.pk]),
            reverse("moderation:remove_content", args=["post", self.post.pk]),
            reverse("moderation:remove_content", args=["comment", self.comment.pk]),
            reverse("moderation:restore_content", args=["post", self.post.pk]),
            reverse("moderation:restore_content", args=["comment", self.comment.pk]),
        ]

    def test_anonymous_is_forbidden_on_every_action_endpoint(self):
        for url in self._endpoints():
            with self.subTest(url=url):
                resp = self.client.post(url, {"reason": "x"})
                self.assertEqual(resp.status_code, 403)

    def test_regular_user_is_forbidden_on_every_action_endpoint(self):
        self.client.force_login(self.regular)
        for url in self._endpoints():
            with self.subTest(url=url):
                resp = self.client.post(url, {"reason": "x"})
                self.assertEqual(resp.status_code, 403)

    def test_no_audit_rows_after_a_full_blocked_sweep(self):
        # After both anon and regular hammer every endpoint, nothing persisted.
        self.client.post(self._endpoints()[0], {"reason": "x"})  # anon
        self.client.force_login(self.regular)
        for url in self._endpoints():
            self.client.post(url, {"reason": "x"})
        self.assertEqual(Warning.objects.count(), 0)
        self.assertEqual(Silence.objects.count(), 0)
        self.assertEqual(Ban.objects.count(), 0)
        self.assertEqual(ContentAction.objects.count(), 0)
        self.target.refresh_from_db()
        self.assertTrue(self.target.is_active)


# --------------------------------------------------------------------------- #
# context_processors.moderation_flags — unit tests via RequestFactory          #
# --------------------------------------------------------------------------- #
class ModerationFlagsUnitTests(_ModerationFixtureMixin, TestCase):
    """``moderation_flags(request)`` returns the right booleans per identity."""

    def setUp(self):
        super().setUp()
        self.rf = RequestFactory()

    def _flags_for(self, user):
        request = self.rf.get("/")
        request.user = user
        return moderation_flags(request)

    def test_anonymous_is_neither_moderator_nor_creator(self):
        flags = self._flags_for(AnonymousUser())
        self.assertEqual(
            flags, {"is_moderator": False, "is_creator": False}
        )

    def test_regular_user_is_neither(self):
        flags = self._flags_for(self.regular)
        self.assertFalse(flags["is_moderator"])
        self.assertFalse(flags["is_creator"])

    def test_collaborator_is_neither(self):
        flags = self._flags_for(self.collaborator)
        self.assertFalse(flags["is_moderator"])
        self.assertFalse(flags["is_creator"])

    def test_founder_is_neither(self):
        flags = self._flags_for(self.founder)
        self.assertFalse(flags["is_moderator"])
        self.assertFalse(flags["is_creator"])

    def test_moderator_is_moderator_but_not_creator(self):
        flags = self._flags_for(self.moderator)
        self.assertTrue(flags["is_moderator"])
        self.assertFalse(flags["is_creator"])

    def test_creator_is_both_moderator_and_creator(self):
        # Creator outranks Moderator, so is_moderator must also be True.
        flags = self._flags_for(self.creator)
        self.assertTrue(flags["is_moderator"])
        self.assertTrue(flags["is_creator"])

    def test_returns_plain_bools_not_truthy_objects(self):
        # The processor coerces to bool; assert exact types so templates and any
        # JSON-ish consumers get real booleans, never a Level/object.
        flags = self._flags_for(self.moderator)
        self.assertIs(flags["is_moderator"], True)
        self.assertIs(flags["is_creator"], False)

    def test_missing_user_attribute_is_treated_as_anonymous(self):
        # The processor uses getattr(request, "user", None) defensively.
        request = self.rf.get("/")
        # No request.user assigned at all.
        flags = moderation_flags(request)
        self.assertEqual(
            flags, {"is_moderator": False, "is_creator": False}
        )

    def test_anonymous_does_not_trigger_is_at_least_call(self):
        # AnonymousUser has no ``is_at_least``; the processor must short-circuit
        # on the auth check and never touch it (else this would AttributeError).
        flags = self._flags_for(AnonymousUser())
        self.assertFalse(flags["is_moderator"])  # reached here without raising


# --------------------------------------------------------------------------- #
# context_processors.moderation_flags — integration via the rendered nav       #
# --------------------------------------------------------------------------- #
class ModerationFlagsRenderedNavTests(_ModerationFixtureMixin, TestCase):
    """The flags drive nav links in ``templates/base.html``:

    - ``is_moderator`` shows the *moderation* link,
    - ``is_creator`` shows the *manage* link.

    The forum index extends base.html, so we render it and inspect the nav.
    """

    def setUp(self):
        super().setUp()
        self.index_url = reverse("forum:index")
        self.mod_link = reverse("moderation:dashboard")
        self.manage_link = reverse("forum:manage_topics")

    def test_anonymous_nav_has_no_mod_or_manage_links(self):
        resp = self.client.get(self.index_url)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["is_moderator"])
        self.assertFalse(resp.context["is_creator"])
        body = resp.content.decode()
        self.assertNotIn(f'href="{self.mod_link}"', body)
        self.assertNotIn(f'href="{self.manage_link}"', body)

    def test_regular_user_nav_has_no_mod_or_manage_links(self):
        self.client.force_login(self.regular)
        resp = self.client.get(self.index_url)
        self.assertFalse(resp.context["is_moderator"])
        self.assertFalse(resp.context["is_creator"])
        body = resp.content.decode()
        self.assertNotIn(f'href="{self.mod_link}"', body)

    def test_moderator_nav_shows_moderation_but_not_manage(self):
        self.client.force_login(self.moderator)
        resp = self.client.get(self.index_url)
        self.assertTrue(resp.context["is_moderator"])
        self.assertFalse(resp.context["is_creator"])
        body = resp.content.decode()
        self.assertIn(f'href="{self.mod_link}"', body)
        self.assertNotIn(f'href="{self.manage_link}"', body)

    def test_creator_nav_shows_both_links(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.index_url)
        self.assertTrue(resp.context["is_moderator"])
        self.assertTrue(resp.context["is_creator"])
        body = resp.content.decode()
        self.assertIn(f'href="{self.mod_link}"', body)
        self.assertIn(f'href="{self.manage_link}"', body)
