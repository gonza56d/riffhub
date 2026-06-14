"""Tests for the "banned-author display" behaviour.

Users are never deleted — misbehaving users are *banned* (an active, un-lifted
``moderation.Ban``). A banned user's NAME must still appear everywhere their
content shows, but their profile becomes inaccessible:

* ``User.is_banned`` is ``True`` while an un-lifted ban exists (``False``
  normally, and again once the ban is lifted);
* ``/u/<username>/`` returns 404 for a banned user — for everyone, with no
  moderator exception;
* on a banned user's content (forum post/comment, DM thread) their name still
  renders, but NOT as a link to their (now 404) profile, while a non-banned
  author's name IS rendered as a profile link.

These are HTTP/template tests driving the real views through
``django.test.Client``.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import SiteConfiguration
from forum.models import Comment, Post, Subtopic, Topic
from messaging.models import Conversation, DirectMessage
from moderation import services as mod
from moderation.models import Ban

User = get_user_model()


def make_user(username, **kwargs):
    """Create a confirmed regular user; override flags via kwargs."""
    defaults = {
        "email": f"{username}@example.com",
        "password": "pw-not-used-by-force-login",
        "email_confirmed": True,
    }
    field_overrides = {
        k: kwargs.pop(k)
        for k in list(kwargs)
        if k in {"is_active", "is_community_moderator", "is_riffhub_creator"}
    }
    defaults.update(kwargs)
    user = User.objects.create_user(username=username, **defaults)
    if field_overrides:
        for key, value in field_overrides.items():
            setattr(user, key, value)
        user.save()
    return user


def ban_row(target, issued_by, *, reason="trolling"):
    """Create an active (un-lifted) ban directly, leaving ``is_active`` alone so
    the test exercises ``is_banned`` via the ban row, not deactivation."""
    return Ban.objects.create(target=target, issued_by=issued_by, reason=reason)


# --------------------------------------------------------------------------- #
# User.is_banned property
# --------------------------------------------------------------------------- #
class IsBannedPropertyTests(TestCase):
    def test_user_is_not_banned_by_default(self):
        user = make_user("clean")
        self.assertFalse(user.is_banned)

    def test_user_is_banned_with_active_ban(self):
        mod_user = make_user("themod", is_community_moderator=True)
        user = make_user("badactor")
        ban_row(user, mod_user)
        self.assertTrue(user.is_banned)

    def test_user_is_not_banned_once_ban_is_lifted(self):
        creator = make_user("boss", is_riffhub_creator=True)
        user = make_user("reformed")
        ban = ban_row(user, creator)
        self.assertTrue(user.is_banned)
        ban.lifted_at = timezone.now()
        ban.save(update_fields=["lifted_at"])
        self.assertFalse(User.objects.get(pk=user.pk).is_banned)

    def test_is_banned_via_moderation_service_ban(self):
        mod_user = make_user("svcmod", is_community_moderator=True)
        target = make_user("svctarget")
        mod.ban(mod_user, target, "spam")
        self.assertTrue(User.objects.get(pk=target.pk).is_banned)


# --------------------------------------------------------------------------- #
# Profile page 404s for a banned user
# --------------------------------------------------------------------------- #
class BannedProfile404Tests(TestCase):
    def setUp(self):
        self.mod = make_user("modder", is_community_moderator=True)

    def test_normal_user_profile_returns_200(self):
        make_user("regularjoe")
        resp = self.client.get(reverse("profile", args=["regularjoe"]))
        self.assertEqual(resp.status_code, 200)

    def test_banned_user_profile_returns_404_to_anonymous(self):
        target = make_user("gonzo")
        ban_row(target, self.mod)
        resp = self.client.get(reverse("profile", args=["gonzo"]))
        self.assertEqual(resp.status_code, 404)

    def test_banned_user_profile_returns_404_even_to_moderator(self):
        # The profile is inaccessible to EVERYONE — no moderator exception.
        target = make_user("gonzo2")
        ban_row(target, self.mod)
        self.client.force_login(self.mod)
        resp = self.client.get(reverse("profile", args=["gonzo2"]))
        self.assertEqual(resp.status_code, 404)

    def test_lifted_ban_restores_profile_access(self):
        target = make_user("backagain")
        ban = ban_row(target, self.mod)
        ban.lifted_at = timezone.now()
        ban.save(update_fields=["lifted_at"])
        resp = self.client.get(reverse("profile", args=["backagain"]))
        self.assertEqual(resp.status_code, 200)


# --------------------------------------------------------------------------- #
# Forum bylines: banned authors show as plain text, not links
# --------------------------------------------------------------------------- #
class ForumBannedBylineTests(TestCase):
    def setUp(self):
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()
        self.mod = make_user("forummod", is_community_moderator=True)
        self.topic = Topic.objects.create(name="Gear")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")
        self.good = make_user("gooduser")
        self.bad = make_user("baduser")
        self.post = Post.objects.create(
            subtopic=self.subtopic,
            author=self.bad,
            title="Banned author thread",
            body="body",
        )
        self.comment = Comment.objects.create(
            post=self.post, author=self.good, body="nice riff"
        )

    def test_post_detail_banned_author_name_shown_without_link(self):
        ban_row(self.bad, self.mod)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 200)
        # name still visible ...
        self.assertContains(resp, "baduser")
        # ... but no link to the (now 404) profile.
        bad_url = reverse("profile", args=["baduser"])
        self.assertNotContains(resp, f'href="{bad_url}"')
        self.assertContains(resp, '<span class="user-banned">baduser</span>')

    def test_post_detail_non_banned_comment_author_is_linked(self):
        ban_row(self.bad, self.mod)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        good_url = reverse("profile", args=["gooduser"])
        # the non-banned commenter is still a profile link.
        self.assertContains(resp, f'href="{good_url}"')

    def test_subtopic_list_banned_author_name_shown_without_link(self):
        ban_row(self.bad, self.mod)
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "baduser")
        bad_url = reverse("profile", args=["baduser"])
        self.assertNotContains(resp, f'href="{bad_url}"')

    def test_post_detail_author_linked_before_ban(self):
        # Sanity: before any ban, the author byline IS a profile link.
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        bad_url = reverse("profile", args=["baduser"])
        self.assertContains(resp, f'href="{bad_url}"')


# --------------------------------------------------------------------------- #
# DM thread: banned counterpart shows as plain text, not a link
# --------------------------------------------------------------------------- #
class MessagingBannedThreadTests(TestCase):
    def setUp(self):
        self.mod = make_user("dmmod", is_community_moderator=True)
        self.me = make_user("me_dm")
        self.other = make_user("other_dm")
        self.conversation, _ = Conversation.for_pair(self.me, self.other)
        DirectMessage.objects.create(
            conversation=self.conversation, sender=self.other, body="hi there"
        )

    def test_thread_header_links_non_banned_counterpart(self):
        self.client.force_login(self.me)
        resp = self.client.get(
            reverse("messaging:thread", args=[self.other.username])
        )
        self.assertEqual(resp.status_code, 200)
        other_url = reverse("profile", args=["other_dm"])
        self.assertContains(resp, f'href="{other_url}"')

    def test_thread_header_banned_counterpart_name_without_link(self):
        ban_row(self.other, self.mod)
        self.client.force_login(self.me)
        resp = self.client.get(
            reverse("messaging:thread", args=[self.other.username])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "other_dm")
        other_url = reverse("profile", args=["other_dm"])
        self.assertNotContains(resp, f'href="{other_url}"')
        self.assertContains(resp, '<span class="user-banned">other_dm</span>')
