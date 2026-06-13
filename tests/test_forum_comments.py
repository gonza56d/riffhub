"""Tests for forum comment creation.

Covers ``forum.services.create_comment`` (the business rules) and the HTMX
endpoint ``forum.views.comment_create`` mounted at
``/forum/post/<pk>/comment/`` (``forum:comment_create``).

PRODUCT.md rules exercised here:
  * A logged-in user can comment; the comment is created, the
    ``forum/_comment.html`` fragment is returned, activity is bumped on the
    subtopic AND its parent topic (+1 each — "ANY action counts as +1"), and
    the author earns ``REP_COMMENT_CREATED`` reputation.
  * An empty body is rejected before any object is created (400).
  * Anonymous users cannot comment (403).
  * Silenced or banned users cannot comment — ``create_comment`` raises
    ``PermissionDenied`` and the view turns that into a 403.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from forum.constants import REP_COMMENT_CREATED
from forum.models import Comment, Post, Subtopic, Topic
from forum.services import create_comment
from moderation.models import Ban, Silence

User = get_user_model()


def _make_user(username, **extra):
    """Create a confirmed, active regular user (the default participant)."""
    defaults = {
        "email": f"{username}@example.com",
        "password": "pw-not-checked-by-force-login",
        "email_confirmed": True,
    }
    defaults.update(extra)
    password = defaults.pop("password")
    user = User.objects.create_user(username=username, password=password, **defaults)
    return user


class ForumCommentTestBase(TestCase):
    """Shared minimal fixtures: a topic → subtopic → post by ``author``."""

    def setUp(self):
        self.client = Client()
        self.topic = Topic.objects.create(name="Gear")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")
        # The post author and a separate commenter (so we don't have to worry
        # about self-interaction rules, which don't apply to commenting anyway).
        self.author = _make_user("threadstarter")
        self.commenter = _make_user("commenter")
        self.post = Post.objects.create(
            subtopic=self.subtopic,
            author=self.author,
            title="Jackson Dinky vs Ibanez RG",
            body="Which superstrat reigns supreme?",
        )

    def comment_url(self, post=None):
        return reverse("forum:comment_create", args=[(post or self.post).pk])


# ---------------------------------------------------------------------------
# Service-level tests (forum.services.create_comment)
# ---------------------------------------------------------------------------
class CreateCommentServiceTests(ForumCommentTestBase):
    def test_create_comment_persists_with_expected_fields(self):
        comment = create_comment(
            post=self.post, author=self.commenter, body="6L6 all the way."
        )
        self.assertIsInstance(comment, Comment)
        self.assertEqual(Comment.objects.count(), 1)
        comment.refresh_from_db()
        self.assertEqual(comment.post_id, self.post.pk)
        self.assertEqual(comment.author_id, self.commenter.pk)
        self.assertEqual(comment.body, "6L6 all the way.")
        # Comments are body-only: no removal by default.
        self.assertFalse(comment.is_removed)

    def test_create_comment_bumps_activity_on_subtopic_and_topic(self):
        # Creating the Post via the ORM (in setUp) did NOT route through the
        # service, so activity starts at zero — a clean baseline.
        self.assertEqual(self.subtopic.activity_count, 0)
        self.assertEqual(self.topic.activity_count, 0)

        create_comment(post=self.post, author=self.commenter, body="Nice tone.")

        self.subtopic.refresh_from_db()
        self.topic.refresh_from_db()
        # ANY action == +1 on the subtopic AND its parent topic (PRODUCT.md).
        self.assertEqual(self.subtopic.activity_count, 1)
        self.assertEqual(self.topic.activity_count, 1)

    def test_each_comment_increments_activity_again(self):
        create_comment(post=self.post, author=self.commenter, body="one")
        create_comment(post=self.post, author=self.author, body="two")
        self.subtopic.refresh_from_db()
        self.topic.refresh_from_db()
        self.assertEqual(self.subtopic.activity_count, 2)
        self.assertEqual(self.topic.activity_count, 2)

    def test_create_comment_awards_author_reputation(self):
        start = self.commenter.reputation_score
        create_comment(post=self.post, author=self.commenter, body="EL34s sound better")
        self.commenter.refresh_from_db()
        self.assertEqual(
            self.commenter.reputation_score, start + REP_COMMENT_CREATED
        )
        # Document the intended weight from PRODUCT.md (comment == +1).
        self.assertEqual(REP_COMMENT_CREATED, 1)

    def test_author_may_comment_on_own_post(self):
        # Commenting on your own post is allowed (only voting/reacting on your
        # own content is forbidden). Reputation should still accrue.
        start = self.author.reputation_score
        comment = create_comment(
            post=self.post, author=self.author, body="Replying to myself."
        )
        self.assertEqual(comment.author_id, self.author.pk)
        self.author.refresh_from_db()
        self.assertEqual(self.author.reputation_score, start + REP_COMMENT_CREATED)

    def test_empty_body_raises_validation_error_and_creates_nothing(self):
        from django.core.exceptions import ValidationError

        # ``create_comment`` runs ``full_clean``; a blank body is invalid.
        with self.assertRaises(ValidationError):
            create_comment(post=self.post, author=self.commenter, body="")
        self.assertEqual(Comment.objects.count(), 0)
        # The failed creation must not have bumped activity (atomic).
        self.subtopic.refresh_from_db()
        self.assertEqual(self.subtopic.activity_count, 0)

    def test_silenced_user_blocked_with_permission_denied(self):
        Silence.objects.create(
            target=self.commenter,
            issued_by=self.author,
            reason="threats",
            sequence=1,
            starts_at=timezone.now(),
            ends_at=timezone.now() + timezone.timedelta(weeks=1),
            is_permanent=False,
        )
        with self.assertRaises(PermissionDenied):
            create_comment(post=self.post, author=self.commenter, body="still here")
        self.assertEqual(Comment.objects.count(), 0)

    def test_permanently_silenced_user_blocked(self):
        Silence.objects.create(
            target=self.commenter,
            issued_by=self.author,
            reason="repeat offender",
            sequence=3,
            starts_at=timezone.now(),
            ends_at=None,
            is_permanent=True,
            is_public_flag=True,
        )
        with self.assertRaises(PermissionDenied):
            create_comment(post=self.post, author=self.commenter, body="hi")
        self.assertEqual(Comment.objects.count(), 0)

    def test_expired_silence_does_not_block(self):
        # An elapsed (non-permanent) silence is no longer active -> may comment.
        Silence.objects.create(
            target=self.commenter,
            issued_by=self.author,
            reason="old, served",
            sequence=1,
            starts_at=timezone.now() - timezone.timedelta(weeks=2),
            ends_at=timezone.now() - timezone.timedelta(days=1),
            is_permanent=False,
        )
        comment = create_comment(
            post=self.post, author=self.commenter, body="done my time"
        )
        self.assertEqual(Comment.objects.count(), 1)
        self.assertEqual(comment.body, "done my time")

    def test_banned_user_blocked_with_permission_denied(self):
        # A ban deactivates the account; ``can_participate`` then refuses.
        Ban.objects.create(
            target=self.commenter, issued_by=self.author, reason="illegal content"
        )
        self.commenter.is_active = False
        self.commenter.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            create_comment(post=self.post, author=self.commenter, body="let me in")
        self.assertEqual(Comment.objects.count(), 0)

    def test_active_ban_record_blocks_even_if_active_flag_left_set(self):
        # ``can_participate`` also rejects an active Ban row directly, not only
        # via is_active. (Belt-and-suspenders per moderation.services.is_banned.)
        Ban.objects.create(
            target=self.commenter, issued_by=self.author, reason="threats"
        )
        # Intentionally leave is_active=True to exercise the Ban-row branch.
        self.assertTrue(self.commenter.is_active)
        with self.assertRaises(PermissionDenied):
            create_comment(post=self.post, author=self.commenter, body="nope")
        self.assertEqual(Comment.objects.count(), 0)


# ---------------------------------------------------------------------------
# View-level tests (HTMX endpoint /forum/post/<pk>/comment/)
# ---------------------------------------------------------------------------
class CommentCreateViewTests(ForumCommentTestBase):
    def test_logged_in_post_creates_comment_and_returns_fragment(self):
        self.client.force_login(self.commenter)
        resp = self.client.post(
            self.comment_url(),
            {"body": "Dinkies feel faster to me."},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        # The endpoint returns the single-comment fragment, not a full page.
        self.assertTemplateUsed(resp, "forum/_comment.html")
        self.assertEqual(Comment.objects.count(), 1)
        comment = Comment.objects.get()
        self.assertEqual(comment.author_id, self.commenter.pk)
        self.assertEqual(comment.post_id, self.post.pk)
        # Fragment should render the new comment's body and a stable anchor id.
        body = resp.content.decode()
        self.assertIn("Dinkies feel faster to me.", body)
        self.assertIn(f'id="comment-{comment.pk}"', body)

    def test_post_bumps_activity_and_reputation_through_view(self):
        self.client.force_login(self.commenter)
        start_rep = self.commenter.reputation_score
        resp = self.client.post(
            self.comment_url(),
            {"body": "Tone is in the fingers."},
        )
        self.assertEqual(resp.status_code, 200)

        self.subtopic.refresh_from_db()
        self.topic.refresh_from_db()
        self.commenter.refresh_from_db()
        self.assertEqual(self.subtopic.activity_count, 1)
        self.assertEqual(self.topic.activity_count, 1)
        self.assertEqual(
            self.commenter.reputation_score, start_rep + REP_COMMENT_CREATED
        )

    def test_body_is_stripped_of_surrounding_whitespace(self):
        self.client.force_login(self.commenter)
        resp = self.client.post(self.comment_url(), {"body": "   trimmed   "})
        self.assertEqual(resp.status_code, 200)
        comment = Comment.objects.get()
        self.assertEqual(comment.body, "trimmed")

    def test_empty_body_returns_400_and_creates_nothing(self):
        self.client.force_login(self.commenter)
        resp = self.client.post(self.comment_url(), {"body": ""})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Comment.objects.count(), 0)

    def test_whitespace_only_body_returns_400(self):
        self.client.force_login(self.commenter)
        resp = self.client.post(self.comment_url(), {"body": "    \n\t  "})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Comment.objects.count(), 0)

    def test_missing_body_field_returns_400(self):
        self.client.force_login(self.commenter)
        resp = self.client.post(self.comment_url(), {})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Comment.objects.count(), 0)

    def test_anonymous_post_returns_403_and_creates_nothing(self):
        resp = self.client.post(self.comment_url(), {"body": "I am anonymous"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Comment.objects.count(), 0)

    def test_get_request_not_allowed(self):
        # The endpoint is POST-only (``@require_POST``).
        self.client.force_login(self.commenter)
        resp = self.client.get(self.comment_url())
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(Comment.objects.count(), 0)

    def test_comment_on_missing_post_returns_404(self):
        self.client.force_login(self.commenter)
        resp = self.client.post(
            reverse("forum:comment_create", args=[999999]),
            {"body": "ghost thread"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(Comment.objects.count(), 0)

    def test_silenced_user_post_returns_403(self):
        Silence.objects.create(
            target=self.commenter,
            issued_by=self.author,
            reason="threatening another user",
            sequence=1,
            starts_at=timezone.now(),
            ends_at=timezone.now() + timezone.timedelta(weeks=1),
            is_permanent=False,
        )
        self.client.force_login(self.commenter)
        resp = self.client.post(self.comment_url(), {"body": "still talking"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Comment.objects.count(), 0)

    def test_permanently_silenced_user_post_returns_403(self):
        Silence.objects.create(
            target=self.commenter,
            issued_by=self.author,
            reason="third strike",
            sequence=3,
            starts_at=timezone.now(),
            ends_at=None,
            is_permanent=True,
            is_public_flag=True,
        )
        self.client.force_login(self.commenter)
        resp = self.client.post(self.comment_url(), {"body": "anyone there"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Comment.objects.count(), 0)

    def test_banned_user_post_returns_403(self):
        # force_login works even on an inactive user; the silence/ban guard in
        # the service is what rejects the comment. A banned account is inactive,
        # and ``can_participate`` refuses inactive users.
        Ban.objects.create(
            target=self.commenter, issued_by=self.author, reason="porn"
        )
        self.commenter.is_active = False
        self.commenter.save(update_fields=["is_active"])
        self.client.force_login(self.commenter)
        resp = self.client.post(self.comment_url(), {"body": "let me back in"})
        # An inactive user is not authenticated by the auth middleware, so the
        # view's own ``is_authenticated`` guard returns the "sign in" 403.
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Comment.objects.count(), 0)

    def test_active_ban_row_blocks_active_user_post_returns_403(self):
        # An active Ban row should block even when is_active is still True:
        # the service's ``can_participate`` -> ``is_banned`` check catches it.
        Ban.objects.create(
            target=self.commenter, issued_by=self.author, reason="threats"
        )
        self.assertTrue(self.commenter.is_active)
        self.client.force_login(self.commenter)
        resp = self.client.post(self.comment_url(), {"body": "hello?"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Comment.objects.count(), 0)

    def test_successful_comment_appears_on_post_detail_page(self):
        self.client.force_login(self.commenter)
        self.client.post(self.comment_url(), {"body": "Great thread!"})
        detail = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Great thread!")
