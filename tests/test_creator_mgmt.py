"""Tests for the Creator-only forum topic/subtopic management UI (/forum/manage/...).

Covers (PRODUCT.md "Riffhub Creator … create/edit/delete The Forum Section's
categories and subcategories"):

* access control on /forum/manage/ — only a Riffhub Creator may reach it; every
  lower level (Moderator, regular, anonymous) gets 403;
* topic/subtopic create/edit/delete happy paths for a Creator;
* non-Creator POSTs are rejected (403) and leave the DB untouched;
* duplicate topic names and duplicate subtopic-under-topic names are handled
  gracefully (a redirect, never a 500);
* deleting a topic cascades to its subtopics and their posts.

All HTTP is exercised through ``django.test.Client``; the Creator gate lives in
``forum.views._require_creator`` which raises ``PermissionDenied`` (-> 403).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import SiteConfiguration
from forum.models import Comment, Post, Subtopic, Topic

User = get_user_model()


class CreatorManagementBase(TestCase):
    """Shared fixtures: configured thresholds + one user per relevant level."""

    def setUp(self):
        # Thresholds must be explicitly configured (PRODUCT.md: no silent
        # default). Set them so level derivation never raises while we build
        # users; the management gate only depends on the granted role flags.
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()

        self.creator = User.objects.create_user(
            username="creator",
            email="creator@example.com",
            password="pw-creator-123",
            email_confirmed=True,
            is_riffhub_creator=True,
        )
        self.moderator = User.objects.create_user(
            username="moderator",
            email="moderator@example.com",
            password="pw-moderator-123",
            email_confirmed=True,
            is_community_moderator=True,
        )
        self.regular = User.objects.create_user(
            username="regular",
            email="regular@example.com",
            password="pw-regular-123",
            email_confirmed=True,
        )

        # A starting topic + subtopic to edit / delete in the happy-path tests.
        self.topic = Topic.objects.create(name="Gear", description="Gear talk")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")


class ManageTopicsAccessTests(CreatorManagementBase):
    """GET /forum/manage/ : Creator 200; everyone else 403."""

    def test_creator_can_view_manage_page(self):
        self.client.force_login(self.creator)
        resp = self.client.get(reverse("forum:manage_topics"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "forum/manage/topics.html")
        # The seeded topic should be listed on the page.
        self.assertContains(resp, "Gear")

    def test_moderator_is_forbidden(self):
        self.client.force_login(self.moderator)
        resp = self.client.get(reverse("forum:manage_topics"))
        self.assertEqual(resp.status_code, 403)

    def test_regular_user_is_forbidden(self):
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("forum:manage_topics"))
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_is_forbidden(self):
        resp = self.client.get(reverse("forum:manage_topics"))
        self.assertEqual(resp.status_code, 403)


class TopicCreateTests(CreatorManagementBase):
    """POST /forum/manage/topic/new/ — TopicForm-backed creation."""

    def test_creator_creates_topic(self):
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:topic_create"),
            {"name": "Amps", "description": "Amplifiers"},
        )
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        topic = Topic.objects.get(name="Amps")
        self.assertEqual(topic.description, "Amplifiers")
        # Slug is auto-derived from the name on save.
        self.assertEqual(topic.slug, "amps")

    def test_creator_creates_market_topic_with_flags(self):
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:topic_create"),
            {
                "name": "Side Market",
                "description": "selling",
                "is_market": "on",
                "requires_disclaimer": "on",
            },
        )
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        topic = Topic.objects.get(name="Side Market")
        self.assertTrue(topic.is_market)
        self.assertTrue(topic.requires_disclaimer)

    def test_blank_name_does_not_create_topic(self):
        self.client.force_login(self.creator)
        before = Topic.objects.count()
        resp = self.client.post(reverse("forum:topic_create"), {"name": ""})
        # Form invalid -> still a redirect back to the manage page, no 500.
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        self.assertEqual(Topic.objects.count(), before)

    def test_get_topic_create_is_405(self):
        # topic_create is @require_POST; a GET must not create anything.
        self.client.force_login(self.creator)
        resp = self.client.get(reverse("forum:topic_create"))
        self.assertEqual(resp.status_code, 405)

    def test_non_creator_cannot_create_topic(self):
        self.client.force_login(self.regular)
        before = Topic.objects.count()
        resp = self.client.post(
            reverse("forum:topic_create"), {"name": "Sneaky"}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Topic.objects.count(), before)
        self.assertFalse(Topic.objects.filter(name="Sneaky").exists())

    def test_moderator_cannot_create_topic(self):
        self.client.force_login(self.moderator)
        before = Topic.objects.count()
        resp = self.client.post(
            reverse("forum:topic_create"), {"name": "ModTopic"}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Topic.objects.count(), before)

    def test_anonymous_cannot_create_topic(self):
        before = Topic.objects.count()
        resp = self.client.post(
            reverse("forum:topic_create"), {"name": "AnonTopic"}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Topic.objects.count(), before)


class TopicCreateDuplicateTests(CreatorManagementBase):
    """A duplicate topic name is handled gracefully (no 500, no second row)."""

    def test_duplicate_topic_name_redirects_without_500(self):
        self.client.force_login(self.creator)
        # "Gear" already exists (from setUp).
        resp = self.client.post(
            reverse("forum:topic_create"), {"name": "Gear"}
        )
        # Topic.name is unique -> the ModelForm reports it invalid, so the view
        # redirects back rather than raising a 500 / IntegrityError.
        self.assertNotEqual(resp.status_code, 500)
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        self.assertEqual(Topic.objects.filter(name="Gear").count(), 1)

    def test_duplicate_topic_name_follow_shows_no_error_page(self):
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:topic_create"), {"name": "Gear"}, follow=True
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "forum/manage/topics.html")


class TopicEditTests(CreatorManagementBase):
    """GET renders the edit form; POST updates the topic."""

    def test_creator_gets_edit_form(self):
        self.client.force_login(self.creator)
        resp = self.client.get(
            reverse("forum:topic_edit", args=[self.topic.pk])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "forum/manage/topic_form.html")

    def test_creator_updates_topic(self):
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:topic_edit", args=[self.topic.pk]),
            {"name": "Gear & Tools", "description": "renamed"},
        )
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.name, "Gear & Tools")
        self.assertEqual(self.topic.description, "renamed")

    def test_edit_missing_topic_is_404(self):
        self.client.force_login(self.creator)
        resp = self.client.get(reverse("forum:topic_edit", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_non_creator_cannot_get_edit_form(self):
        self.client.force_login(self.regular)
        resp = self.client.get(
            reverse("forum:topic_edit", args=[self.topic.pk])
        )
        self.assertEqual(resp.status_code, 403)

    def test_non_creator_post_does_not_edit(self):
        self.client.force_login(self.moderator)
        resp = self.client.post(
            reverse("forum:topic_edit", args=[self.topic.pk]),
            {"name": "HACKED"},
        )
        self.assertEqual(resp.status_code, 403)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.name, "Gear")

    def test_anonymous_cannot_get_edit_form(self):
        resp = self.client.get(
            reverse("forum:topic_edit", args=[self.topic.pk])
        )
        self.assertEqual(resp.status_code, 403)


class TopicDeleteTests(CreatorManagementBase):
    """POST deletes a topic; GET is rejected; non-Creators cannot delete."""

    def test_creator_deletes_topic(self):
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:topic_delete", args=[self.topic.pk])
        )
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        self.assertFalse(Topic.objects.filter(pk=self.topic.pk).exists())

    def test_get_topic_delete_is_405_and_keeps_topic(self):
        self.client.force_login(self.creator)
        resp = self.client.get(
            reverse("forum:topic_delete", args=[self.topic.pk])
        )
        self.assertEqual(resp.status_code, 405)
        self.assertTrue(Topic.objects.filter(pk=self.topic.pk).exists())

    def test_delete_missing_topic_is_404(self):
        self.client.force_login(self.creator)
        resp = self.client.post(reverse("forum:topic_delete", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_regular_cannot_delete_topic(self):
        self.client.force_login(self.regular)
        resp = self.client.post(
            reverse("forum:topic_delete", args=[self.topic.pk])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Topic.objects.filter(pk=self.topic.pk).exists())

    def test_moderator_cannot_delete_topic(self):
        self.client.force_login(self.moderator)
        resp = self.client.post(
            reverse("forum:topic_delete", args=[self.topic.pk])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Topic.objects.filter(pk=self.topic.pk).exists())

    def test_anonymous_cannot_delete_topic(self):
        resp = self.client.post(
            reverse("forum:topic_delete", args=[self.topic.pk])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Topic.objects.filter(pk=self.topic.pk).exists())


class TopicDeleteCascadeTests(CreatorManagementBase):
    """Deleting a topic removes its subtopics and their posts/comments."""

    def test_delete_topic_cascades_subtopics_posts_and_comments(self):
        # Build: topic -> subtopic (self.subtopic) -> post -> comment, plus a
        # second subtopic, all of which must be gone after the topic delete.
        extra_sub = Subtopic.objects.create(topic=self.topic, name="Basses")
        post = Post.objects.create(
            subtopic=self.subtopic,
            author=self.regular,
            title="My amp rig",
            body="Body text",
        )
        comment = Comment.objects.create(
            post=post, author=self.regular, body="Nice rig"
        )
        sub_pk = self.subtopic.pk
        extra_sub_pk = extra_sub.pk
        post_pk = post.pk
        comment_pk = comment.pk

        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:topic_delete", args=[self.topic.pk])
        )
        self.assertRedirects(resp, reverse("forum:manage_topics"))

        self.assertFalse(Topic.objects.filter(pk=self.topic.pk).exists())
        self.assertFalse(Subtopic.objects.filter(pk=sub_pk).exists())
        self.assertFalse(Subtopic.objects.filter(pk=extra_sub_pk).exists())
        self.assertFalse(Post.objects.filter(pk=post_pk).exists())
        self.assertFalse(Comment.objects.filter(pk=comment_pk).exists())

    def test_delete_topic_leaves_other_topics_untouched(self):
        other = Topic.objects.create(name="State Of Art")
        other_sub = Subtopic.objects.create(topic=other, name="Metal")

        self.client.force_login(self.creator)
        self.client.post(reverse("forum:topic_delete", args=[self.topic.pk]))

        self.assertTrue(Topic.objects.filter(pk=other.pk).exists())
        self.assertTrue(Subtopic.objects.filter(pk=other_sub.pk).exists())


class SubtopicCreateTests(CreatorManagementBase):
    """POST /forum/manage/topic/<pk>/subtopic/new/ — direct Subtopic creation."""

    def test_creator_adds_subtopic(self):
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[self.topic.pk]),
            {"name": "Pedals"},
        )
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        sub = Subtopic.objects.get(topic=self.topic, name="Pedals")
        self.assertEqual(sub.slug, "pedals")

    def test_blank_subtopic_name_creates_nothing(self):
        self.client.force_login(self.creator)
        before = Subtopic.objects.filter(topic=self.topic).count()
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[self.topic.pk]),
            {"name": "   "},
        )
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        self.assertEqual(
            Subtopic.objects.filter(topic=self.topic).count(), before
        )

    def test_get_subtopic_create_is_405(self):
        self.client.force_login(self.creator)
        resp = self.client.get(
            reverse("forum:subtopic_create", args=[self.topic.pk])
        )
        self.assertEqual(resp.status_code, 405)

    def test_create_subtopic_under_missing_topic_is_404(self):
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[999999]),
            {"name": "Orphan"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_non_creator_cannot_add_subtopic(self):
        self.client.force_login(self.regular)
        before = Subtopic.objects.filter(topic=self.topic).count()
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[self.topic.pk]),
            {"name": "Sneaky"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            Subtopic.objects.filter(topic=self.topic).count(), before
        )
        self.assertFalse(
            Subtopic.objects.filter(topic=self.topic, name="Sneaky").exists()
        )

    def test_moderator_cannot_add_subtopic(self):
        self.client.force_login(self.moderator)
        before = Subtopic.objects.filter(topic=self.topic).count()
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[self.topic.pk]),
            {"name": "ModSub"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            Subtopic.objects.filter(topic=self.topic).count(), before
        )

    def test_anonymous_cannot_add_subtopic(self):
        before = Subtopic.objects.filter(topic=self.topic).count()
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[self.topic.pk]),
            {"name": "AnonSub"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            Subtopic.objects.filter(topic=self.topic).count(), before
        )


class SubtopicCreateDuplicateTests(CreatorManagementBase):
    """Duplicate subtopic-under-topic handled gracefully (no 500); same name
    is allowed under a *different* topic (slug is unique per-topic)."""

    def test_duplicate_subtopic_under_same_topic_redirects_without_500(self):
        self.client.force_login(self.creator)
        # "Guitars" already exists under self.topic (from setUp).
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[self.topic.pk]),
            {"name": "Guitars"},
        )
        # The (topic, slug) unique constraint raises IntegrityError which the
        # view catches -> redirect, never a 500.
        self.assertNotEqual(resp.status_code, 500)
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        self.assertEqual(
            Subtopic.objects.filter(topic=self.topic, name="Guitars").count(),
            1,
        )

    def test_duplicate_subtopic_follow_shows_manage_page(self):
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[self.topic.pk]),
            {"name": "Guitars"},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "forum/manage/topics.html")

    def test_same_subtopic_name_allowed_under_different_topic(self):
        other = Topic.objects.create(name="Gear Market")
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[other.pk]),
            {"name": "Guitars"},
        )
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        # "Guitars" now exists under both topics (slug uniqueness is per-topic).
        self.assertTrue(
            Subtopic.objects.filter(topic=other, name="Guitars").exists()
        )
        self.assertTrue(
            Subtopic.objects.filter(topic=self.topic, name="Guitars").exists()
        )


class SubtopicEditTests(CreatorManagementBase):
    """GET renders the form; POST updates name and/or parent topic."""

    def test_creator_gets_edit_form(self):
        self.client.force_login(self.creator)
        resp = self.client.get(
            reverse("forum:subtopic_edit", args=[self.subtopic.pk])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "forum/manage/subtopic_form.html")

    def test_creator_renames_subtopic(self):
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:subtopic_edit", args=[self.subtopic.pk]),
            {"topic": self.topic.pk, "name": "Electric Guitars"},
        )
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        self.subtopic.refresh_from_db()
        self.assertEqual(self.subtopic.name, "Electric Guitars")
        self.assertEqual(self.subtopic.topic_id, self.topic.pk)

    def test_creator_moves_subtopic_to_other_topic(self):
        other = Topic.objects.create(name="Gear Market")
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:subtopic_edit", args=[self.subtopic.pk]),
            {"topic": other.pk, "name": "Guitars"},
        )
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        self.subtopic.refresh_from_db()
        self.assertEqual(self.subtopic.topic_id, other.pk)

    def test_edit_missing_subtopic_is_404(self):
        self.client.force_login(self.creator)
        resp = self.client.get(reverse("forum:subtopic_edit", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_non_creator_cannot_get_subtopic_edit_form(self):
        self.client.force_login(self.regular)
        resp = self.client.get(
            reverse("forum:subtopic_edit", args=[self.subtopic.pk])
        )
        self.assertEqual(resp.status_code, 403)

    def test_non_creator_post_does_not_edit_subtopic(self):
        self.client.force_login(self.moderator)
        resp = self.client.post(
            reverse("forum:subtopic_edit", args=[self.subtopic.pk]),
            {"topic": self.topic.pk, "name": "HACKED"},
        )
        self.assertEqual(resp.status_code, 403)
        self.subtopic.refresh_from_db()
        self.assertEqual(self.subtopic.name, "Guitars")

    def test_anonymous_cannot_get_subtopic_edit_form(self):
        resp = self.client.get(
            reverse("forum:subtopic_edit", args=[self.subtopic.pk])
        )
        self.assertEqual(resp.status_code, 403)


class SubtopicDeleteTests(CreatorManagementBase):
    """POST deletes a subtopic (cascading its posts); GET/non-Creator rejected."""

    def test_creator_deletes_subtopic(self):
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:subtopic_delete", args=[self.subtopic.pk])
        )
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        self.assertFalse(Subtopic.objects.filter(pk=self.subtopic.pk).exists())
        # Parent topic survives a subtopic deletion.
        self.assertTrue(Topic.objects.filter(pk=self.topic.pk).exists())

    def test_delete_subtopic_cascades_its_posts_and_comments(self):
        post = Post.objects.create(
            subtopic=self.subtopic,
            author=self.regular,
            title="Thread",
            body="Body",
        )
        comment = Comment.objects.create(
            post=post, author=self.regular, body="reply"
        )
        post_pk, comment_pk = post.pk, comment.pk

        self.client.force_login(self.creator)
        self.client.post(
            reverse("forum:subtopic_delete", args=[self.subtopic.pk])
        )
        self.assertFalse(Post.objects.filter(pk=post_pk).exists())
        self.assertFalse(Comment.objects.filter(pk=comment_pk).exists())

    def test_get_subtopic_delete_is_405_and_keeps_subtopic(self):
        self.client.force_login(self.creator)
        resp = self.client.get(
            reverse("forum:subtopic_delete", args=[self.subtopic.pk])
        )
        self.assertEqual(resp.status_code, 405)
        self.assertTrue(Subtopic.objects.filter(pk=self.subtopic.pk).exists())

    def test_delete_missing_subtopic_is_404(self):
        self.client.force_login(self.creator)
        resp = self.client.post(
            reverse("forum:subtopic_delete", args=[999999])
        )
        self.assertEqual(resp.status_code, 404)

    def test_regular_cannot_delete_subtopic(self):
        self.client.force_login(self.regular)
        resp = self.client.post(
            reverse("forum:subtopic_delete", args=[self.subtopic.pk])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Subtopic.objects.filter(pk=self.subtopic.pk).exists())

    def test_moderator_cannot_delete_subtopic(self):
        self.client.force_login(self.moderator)
        resp = self.client.post(
            reverse("forum:subtopic_delete", args=[self.subtopic.pk])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Subtopic.objects.filter(pk=self.subtopic.pk).exists())

    def test_anonymous_cannot_delete_subtopic(self):
        resp = self.client.post(
            reverse("forum:subtopic_delete", args=[self.subtopic.pk])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Subtopic.objects.filter(pk=self.subtopic.pk).exists())
