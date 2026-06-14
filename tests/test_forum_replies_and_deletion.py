"""Tests for comment replies (one level + @tagging) and author self-deletion.

Covers the PRODUCT.md additions:

* **Replies** — a comment may reply to a *top-level* comment, never to a reply
  (single level), and may *tag* any non-banned user via ``@username``. The rule
  lives in ``Comment.clean`` / ``forum.services.create_comment``; the HTMX
  endpoint is ``forum:reply_create``.
* **Author deletion** — a user can delete their own post or comment.
    - A deleted **post** disappears for everyone but moderators (content +
      comments), and is auditable at ``/deleted`` (``deleted:index`` /
      ``deleted:subtopic``).
    - A deleted **comment/reply** renders "This message was deleted." for
      everyone; its reactions are preserved and shown; moderators (and Riffhub
      Creators) can reveal the original via ``forum:comment_original``. The
      original body is never sent to non-moderators — they cannot read it from
      the page HTML.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Level
from core.models import SiteConfiguration
from forum.models import Comment, Post, Reaction, Subtopic, Topic
from forum.services import (
    cast_vote,
    create_comment,
    delete_comment,
    delete_post,
    toggle_reaction,
)
from moderation.services import ban

User = get_user_model()

DELETED_TEXT = "This message was deleted."


def _config():
    cfg = SiteConfiguration.get_solo()
    cfg.collaborator_promotion_threshold = 3
    cfg.founder_threshold = 30
    cfg.save()
    return cfg


def make_user(username, **flags):
    defaults = {
        "email": f"{username}@example.com",
        "password": "pw-12345",
        "email_confirmed": True,
    }
    defaults.update(flags)
    return User.objects.create_user(username=username, **defaults)


class ForumThreadBase(TestCase):
    def setUp(self):
        self.client = Client()
        _config()
        self.topic = Topic.objects.create(name="Gear")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")
        self.author = make_user("threadstarter")
        self.commenter = make_user("commenter")
        self.other = make_user("other_user")
        self.post = Post.objects.create(
            subtopic=self.subtopic, author=self.author, title="Strat vs LP", body="?"
        )
        self.root = Comment.objects.create(
            post=self.post, author=self.commenter, body="Strat all day"
        )

    def post_url(self, post=None):
        return reverse("forum:post", args=[(post or self.post).pk])


# ---------------------------------------------------------------------------
# Replies — single level only
# ---------------------------------------------------------------------------
class ReplyServiceTests(ForumThreadBase):
    def test_create_reply_links_to_parent(self):
        reply = create_comment(
            post=self.post, author=self.other, body="Nah, LP", parent=self.root
        )
        self.assertEqual(reply.parent_id, self.root.pk)
        self.assertTrue(reply.is_reply)
        self.assertFalse(self.root.is_reply)
        self.assertIn(reply, self.root.replies.all())

    def test_cannot_reply_to_a_reply(self):
        reply = create_comment(
            post=self.post, author=self.other, body="first", parent=self.root
        )
        with self.assertRaises(ValidationError):
            create_comment(
                post=self.post, author=self.commenter, body="nested", parent=reply
            )
        # Only the one valid reply exists.
        self.assertEqual(Comment.objects.filter(parent__isnull=False).count(), 1)

    def test_reply_must_share_parents_post(self):
        other_post = Post.objects.create(
            subtopic=self.subtopic, author=self.author, title="Other", body="x"
        )
        with self.assertRaises(ValidationError):
            create_comment(
                post=other_post, author=self.other, body="mismatch", parent=self.root
            )


class ReplyViewTests(ForumThreadBase):
    def reply_url(self, parent=None):
        return reverse("forum:reply_create", args=[(parent or self.root).pk])

    def test_reply_endpoint_creates_and_returns_fragment(self):
        self.client.force_login(self.other)
        resp = self.client.post(self.reply_url(), {"body": "I prefer a Les Paul"})
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "forum/_comment.html")
        reply = Comment.objects.get(parent=self.root)
        self.assertEqual(reply.author_id, self.other.pk)
        self.assertContains(resp, "I prefer a Les Paul")

    def test_reply_to_a_reply_via_endpoint_is_rejected(self):
        reply = create_comment(
            post=self.post, author=self.other, body="first", parent=self.root
        )
        self.client.force_login(self.commenter)
        resp = self.client.post(
            reverse("forum:reply_create", args=[reply.pk]), {"body": "nested"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Comment.objects.filter(parent=reply).count(), 0)

    def test_anonymous_cannot_reply(self):
        resp = self.client.post(self.reply_url(), {"body": "anon"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Comment.objects.filter(parent=self.root).exists())

    def test_empty_reply_rejected(self):
        self.client.force_login(self.other)
        resp = self.client.post(self.reply_url(), {"body": "   "})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Comment.objects.filter(parent=self.root).exists())

    def test_reply_renders_nested_under_root_on_post_detail(self):
        create_comment(post=self.post, author=self.other, body="nested reply body", parent=self.root)
        resp = self.client.get(self.post_url())
        self.assertEqual(resp.status_code, 200)
        # One top-level row, with one reply nested under it.
        rows = resp.context["comment_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["replies"]), 1)
        self.assertContains(resp, "nested reply body")

    def test_cannot_reply_to_deleted_root(self):
        delete_comment(self.commenter, self.root)
        self.client.force_login(self.other)
        resp = self.client.post(self.reply_url(), {"body": "too late"})
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Tagging — @mentions of non-banned users only
# ---------------------------------------------------------------------------
class MentionTests(ForumThreadBase):
    def test_mention_of_existing_user_is_stored_and_linked(self):
        reply = create_comment(
            post=self.post,
            author=self.other,
            body=f"hey @{self.author.username} nice thread",
            parent=self.root,
        )
        self.assertIn(self.author, reply.mentions.all())
        resp = self.client.get(self.post_url())
        # Rendered as a profile link.
        self.assertContains(resp, reverse("profile", args=[self.author.username]))
        self.assertContains(resp, f"@{self.author.username}")

    def test_banned_user_cannot_be_tagged(self):
        moderator = make_user("mod_for_ban", is_community_moderator=True)
        banned = make_user("bannedbob")
        ban(moderator, banned, "illegal content")
        reply = create_comment(
            post=self.post,
            author=self.other,
            body=f"@{banned.username} and @{self.author.username}",
            parent=self.root,
        )
        # Only the non-banned user is tagged.
        self.assertNotIn(banned, reply.mentions.all())
        self.assertIn(self.author, reply.mentions.all())

    def test_unknown_handle_is_not_linked(self):
        reply = create_comment(
            post=self.post, author=self.other, body="@ghost123 are you there", parent=self.root
        )
        self.assertEqual(reply.mentions.count(), 0)
        resp = self.client.get(self.post_url())
        # The literal handle shows as plain text, never as a profile link.
        self.assertContains(resp, "@ghost123")
        self.assertNotContains(resp, reverse("profile", args=["ghost123"]))

    def test_comment_body_escapes_html(self):
        Comment.objects.all().delete()
        create_comment(post=self.post, author=self.other, body="<script>alert(1)</script>")
        resp = self.client.get(self.post_url())
        self.assertNotContains(resp, "<script>alert(1)</script>")
        self.assertContains(resp, "&lt;script&gt;")


# ---------------------------------------------------------------------------
# Post deletion (author self-service)
# ---------------------------------------------------------------------------
class PostDeletionTests(ForumThreadBase):
    def test_author_can_delete_own_post(self):
        delete_post(self.author, self.post)
        self.post.refresh_from_db()
        self.assertTrue(self.post.is_deleted)
        self.assertEqual(self.post.deleted_by_id, self.author.pk)
        self.assertIsNotNone(self.post.deleted_at)

    def test_non_author_cannot_delete_post_service(self):
        with self.assertRaises(PermissionDenied):
            delete_post(self.other, self.post)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_deleted)

    def test_delete_post_endpoint_redirects_to_subtopic(self):
        self.client.force_login(self.author)
        resp = self.client.post(reverse("forum:post_delete", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.post.refresh_from_db()
        self.assertTrue(self.post.is_deleted)

    def test_delete_post_endpoint_403_for_non_author(self):
        self.client.force_login(self.other)
        resp = self.client.post(reverse("forum:post_delete", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 403)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_deleted)

    def test_anonymous_delete_post_redirects_to_login(self):
        resp = self.client.post(reverse("forum:post_delete", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"])

    def test_deleted_post_is_404_for_anonymous_and_regular(self):
        delete_post(self.author, self.post)
        self.assertEqual(self.client.get(self.post_url()).status_code, 404)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.post_url()).status_code, 404)

    def test_deleted_post_visible_to_moderator_and_creator(self):
        delete_post(self.author, self.post)
        mod = make_user("mod_view", is_community_moderator=True)
        self.client.force_login(mod)
        self.assertEqual(self.client.get(self.post_url()).status_code, 200)
        creator = make_user("creator_view", is_riffhub_creator=True)
        self.client.force_login(creator)
        self.assertEqual(self.client.get(self.post_url()).status_code, 200)

    def test_deleted_post_hidden_from_subtopic_listing(self):
        delete_post(self.author, self.post)
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertNotContains(resp, "Strat vs LP")
        self.assertNotIn(self.post, list(resp.context["posts"]))

    def test_author_loses_access_to_own_deleted_post(self):
        # The author deleting their post can no longer read it either (non-mod).
        delete_post(self.author, self.post)
        self.client.force_login(self.author)
        self.assertEqual(self.client.get(self.post_url()).status_code, 404)


# ---------------------------------------------------------------------------
# Comment deletion (placeholder + reveal-for-mods + preserved reactions)
# ---------------------------------------------------------------------------
class CommentDeletionTests(ForumThreadBase):
    def setUp(self):
        super().setUp()
        # Put a recognisable secret in the comment body so we can assert it never
        # leaks to non-moderators after deletion.
        self.root.body = "SECRET-ORIGINAL-BODY"
        self.root.save(update_fields=["body"])

    def test_author_can_delete_own_comment(self):
        delete_comment(self.commenter, self.root)
        self.root.refresh_from_db()
        self.assertTrue(self.root.is_deleted)

    def test_non_author_cannot_delete_comment(self):
        self.client.force_login(self.other)
        resp = self.client.post(reverse("forum:comment_delete", args=[self.root.pk]))
        self.assertEqual(resp.status_code, 403)
        self.root.refresh_from_db()
        self.assertFalse(self.root.is_deleted)

    def test_delete_comment_endpoint_returns_placeholder_fragment(self):
        self.client.force_login(self.commenter)
        resp = self.client.post(reverse("forum:comment_delete", args=[self.root.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, DELETED_TEXT)
        self.assertNotContains(resp, "SECRET-ORIGINAL-BODY")

    def test_deleted_comment_shows_placeholder_and_hides_body_for_everyone(self):
        delete_comment(self.commenter, self.root)
        # Anonymous
        resp = self.client.get(self.post_url())
        self.assertContains(resp, DELETED_TEXT)
        self.assertNotContains(resp, "SECRET-ORIGINAL-BODY")
        # Regular logged-in user
        self.client.force_login(self.other)
        resp = self.client.get(self.post_url())
        self.assertNotContains(resp, "SECRET-ORIGINAL-BODY")

    def test_deleted_comment_body_hidden_even_from_moderator_in_page(self):
        delete_comment(self.commenter, self.root)
        mod = make_user("mod_secret", is_community_moderator=True)
        self.client.force_login(mod)
        resp = self.client.get(self.post_url())
        # The body is never in the page; the mod must explicitly reveal it.
        self.assertNotContains(resp, "SECRET-ORIGINAL-BODY")
        self.assertContains(resp, "Show Original Message")

    def test_regular_user_has_no_show_original_button(self):
        delete_comment(self.commenter, self.root)
        self.client.force_login(self.other)
        resp = self.client.get(self.post_url())
        self.assertNotContains(resp, "Show Original Message")

    def test_show_original_endpoint_reveals_body_to_moderator(self):
        delete_comment(self.commenter, self.root)
        mod = make_user("mod_reveal", is_community_moderator=True)
        self.client.force_login(mod)
        resp = self.client.get(reverse("forum:comment_original", args=[self.root.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "SECRET-ORIGINAL-BODY")

    def test_show_original_endpoint_reveals_body_to_creator(self):
        delete_comment(self.commenter, self.root)
        creator = make_user("creator_reveal", is_riffhub_creator=True)
        self.client.force_login(creator)
        resp = self.client.get(reverse("forum:comment_original", args=[self.root.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "SECRET-ORIGINAL-BODY")

    def test_show_original_endpoint_403_for_regular_and_anonymous(self):
        delete_comment(self.commenter, self.root)
        url = reverse("forum:comment_original", args=[self.root.pk])
        self.assertEqual(self.client.get(url).status_code, 403)  # anonymous
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 403)  # regular

    def test_show_original_404_when_comment_not_deleted(self):
        mod = make_user("mod_404", is_community_moderator=True)
        self.client.force_login(mod)
        resp = self.client.get(reverse("forum:comment_original", args=[self.root.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_reactions_preserved_and_shown_after_deletion(self):
        toggle_reaction(self.other, self.root, "🔥")
        delete_comment(self.commenter, self.root)
        self.assertEqual(Reaction.objects.filter(object_id=self.root.pk).count(), 1)
        resp = self.client.get(self.post_url())
        self.assertContains(resp, DELETED_TEXT)
        self.assertContains(resp, "🔥")

    def test_cannot_vote_on_deleted_comment(self):
        delete_comment(self.commenter, self.root)
        self.client.force_login(self.other)
        resp = self.client.post(reverse("forum:vote", args=["comment", self.root.pk, "up"]))
        self.assertEqual(resp.status_code, 404)

    def test_cannot_react_on_deleted_comment(self):
        delete_comment(self.commenter, self.root)
        self.client.force_login(self.other)
        resp = self.client.post(
            reverse("forum:react", args=["comment", self.root.pk]), {"emoji": "🤘"}
        )
        self.assertEqual(resp.status_code, 404)

    def test_deleting_root_preserves_its_replies(self):
        reply = create_comment(
            post=self.post, author=self.other, body="surviving reply", parent=self.root
        )
        delete_comment(self.commenter, self.root)
        resp = self.client.get(self.post_url())
        self.assertContains(resp, DELETED_TEXT)
        self.assertContains(resp, "surviving reply")
        rows = resp.context["comment_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["replies"]), 1)
        self.assertEqual(rows[0]["replies"][0]["comment"], reply)


# ---------------------------------------------------------------------------
# /deleted audit area (moderators only)
# ---------------------------------------------------------------------------
class DeletedAuditAreaTests(ForumThreadBase):
    def setUp(self):
        super().setUp()
        delete_post(self.author, self.post)  # one deleted post in self.subtopic

    def test_deleted_index_403_for_anonymous_and_regular(self):
        self.assertEqual(self.client.get(reverse("deleted:index")).status_code, 403)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("deleted:index")).status_code, 403)

    def test_deleted_index_403_for_founder(self):
        founder = make_user("founder_a", is_founder=True)
        self.client.force_login(founder)
        self.assertEqual(self.client.get(reverse("deleted:index")).status_code, 403)

    def test_deleted_index_200_for_moderator_and_lists_tree(self):
        mod = make_user("mod_idx", is_community_moderator=True)
        self.client.force_login(mod)
        resp = self.client.get(reverse("deleted:index"))
        self.assertEqual(resp.status_code, 200)
        # The full live hierarchy is shown.
        self.assertContains(resp, "Gear")
        self.assertContains(resp, "Guitars")
        # The subtopic with a deleted post links into the drill-down.
        self.assertContains(resp, reverse("deleted:subtopic", args=[self.subtopic.pk]))

    def test_deleted_index_200_for_creator(self):
        creator = make_user("creator_idx", is_riffhub_creator=True)
        self.client.force_login(creator)
        self.assertEqual(self.client.get(reverse("deleted:index")).status_code, 200)

    def test_deleted_subtopic_lists_only_deleted_posts(self):
        live = Post.objects.create(
            subtopic=self.subtopic, author=self.author, title="Live thread", body="x"
        )
        mod = make_user("mod_sub", is_community_moderator=True)
        self.client.force_login(mod)
        resp = self.client.get(reverse("deleted:subtopic", args=[self.subtopic.pk]))
        self.assertEqual(resp.status_code, 200)
        posts = list(resp.context["posts"])
        self.assertIn(self.post, posts)
        self.assertNotIn(live, posts)
        self.assertContains(resp, "Strat vs LP")
        self.assertNotContains(resp, "Live thread")

    def test_deleted_subtopic_403_for_regular(self):
        self.client.force_login(self.other)
        resp = self.client.get(reverse("deleted:subtopic", args=[self.subtopic.pk]))
        self.assertEqual(resp.status_code, 403)
