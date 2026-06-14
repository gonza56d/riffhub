"""Tests for moderation.services actions + enforcement (PRODUCT.md "Moderation").

Covers the user-facing moderation actions (warn / silence / ban / lift_ban),
the content actions (move / remove / restore) and the enforcement seam in
``forum.services`` (a silenced or banned user cannot post or comment).

These call ``moderation.services`` directly (unit-style); only fixtures are
built. Permission rules from PRODUCT.md are asserted explicitly:
  * silence escalates 1 week -> 1 month -> permanent (publicly flagged),
  * you cannot silence/ban a Creator or yourself,
  * moderators cannot ban another moderator, but a Creator can,
  * banning deactivates the account; lifting reactivates it.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from accounts.models import Level
from core.models import SiteConfiguration
from forum.models import Comment, Post, Subtopic, Topic
from forum.services import create_comment, create_post
from moderation import services as mod
from moderation.constants import ContentActionType, SILENCE_DURATIONS
from moderation.models import Ban, ContentAction, Silence, Warning

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


class ModerationTestBase(TestCase):
    """Shared actors + a non-market subtopic for enforcement tests."""

    def setUp(self):
        # Thresholds set so Collaborator/Founder derivation never raises.
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()

        self.moderator = make_user("mod", is_community_moderator=True)
        self.creator = make_user("creator", is_riffhub_creator=True)
        self.regular = make_user("regular")
        self.victim = make_user("victim")

        self.topic = Topic.objects.create(name="Gear", is_predefined=True)
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")

    def fresh(self, user):
        """Re-fetch a user so cached related managers (silences) are dropped."""
        return User.objects.get(pk=user.pk)


# ---------------------------------------------------------------------------
# Permission gate (_require_moderator)
# ---------------------------------------------------------------------------
class RequireModeratorTests(ModerationTestBase):
    def test_regular_user_cannot_warn(self):
        with self.assertRaises(PermissionDenied):
            mod.warn(self.regular, self.victim, reason="nope")

    def test_regular_user_cannot_silence(self):
        with self.assertRaises(PermissionDenied):
            mod.silence(self.regular, self.victim, reason="nope")

    def test_regular_user_cannot_ban(self):
        with self.assertRaises(PermissionDenied):
            mod.ban(self.regular, self.victim, reason="nope")

    def test_collaborator_cannot_moderate(self):
        # accepted_submissions_count >= threshold(3) -> Collaborator, still < MODERATOR.
        collaborator = make_user("collab", accepted_submissions_count=5)
        self.assertEqual(self.fresh(collaborator).level, Level.COLLABORATOR)
        with self.assertRaises(PermissionDenied):
            mod.warn(collaborator, self.victim, reason="nope")

    def test_anonymous_like_object_cannot_moderate(self):
        class Anon:
            is_authenticated = False

            def is_at_least(self, level):
                return False

        with self.assertRaises(PermissionDenied):
            mod.warn(Anon(), self.victim, reason="nope")

    def test_creator_may_moderate(self):
        warning = mod.warn(self.creator, self.victim, reason="ok")
        self.assertEqual(warning.issued_by, self.creator)


# ---------------------------------------------------------------------------
# warn
# ---------------------------------------------------------------------------
class WarnTests(ModerationTestBase):
    def test_warn_records_a_warning(self):
        warning = mod.warn(self.moderator, self.victim, reason="off-topic football")
        self.assertEqual(Warning.objects.count(), 1)
        self.assertEqual(warning.target, self.victim)
        self.assertEqual(warning.issued_by, self.moderator)
        self.assertEqual(warning.reason, "off-topic football")
        self.assertIsNone(warning.content)

    def test_warn_can_reference_content(self):
        post = create_post(
            subtopic=self.subtopic,
            author=self.victim,
            title="Footy thread",
            body="not about guitars",
        )
        warning = mod.warn(
            self.moderator, self.victim, reason="unrelated", content=post
        )
        self.assertEqual(warning.content, post)
        self.assertEqual(warning.object_id, post.pk)
        self.assertEqual(
            warning.content_type, ContentType.objects.get_for_model(Post)
        )

    def test_warn_does_not_block_creator_target(self):
        # warn has no level guard on the target (unlike silence/ban).
        warning = mod.warn(self.moderator, self.creator, reason="heads up")
        self.assertEqual(warning.target, self.creator)

    def test_warn_does_not_block_self(self):
        warning = mod.warn(self.moderator, self.moderator, reason="self note")
        self.assertEqual(warning.target, self.moderator)

    def test_multiple_warnings_accumulate(self):
        mod.warn(self.moderator, self.victim, reason="one")
        mod.warn(self.moderator, self.victim, reason="two")
        self.assertEqual(self.victim.warnings.count(), 2)


# ---------------------------------------------------------------------------
# silence — escalation 1 week -> 1 month -> permanent
# ---------------------------------------------------------------------------
class SilenceEscalationTests(ModerationTestBase):
    def test_first_silence_lasts_about_one_week(self):
        before = timezone.now()
        silence = mod.silence(self.moderator, self.victim, reason="threat #1")
        self.assertEqual(silence.sequence, 1)
        self.assertFalse(silence.is_permanent)
        self.assertFalse(silence.is_public_flag)
        self.assertIsNotNone(silence.ends_at)
        expected = before + SILENCE_DURATIONS[1]  # 1 week
        delta = abs((silence.ends_at - expected).total_seconds())
        self.assertLess(delta, 60, "first silence should end ~7 days out")
        # Sanity: roughly a week from now, comfortably more than 6 days.
        self.assertGreater(silence.ends_at, timezone.now() + timedelta(days=6))

    def test_second_silence_lasts_about_one_month(self):
        # An existing 1st silence drives the sequence to 2.
        Silence.objects.create(
            target=self.victim,
            issued_by=self.moderator,
            reason="prior",
            sequence=1,
            starts_at=timezone.now(),
            ends_at=timezone.now() + SILENCE_DURATIONS[1],
        )
        before = timezone.now()
        silence = mod.silence(self.moderator, self.victim, reason="threat #2")
        self.assertEqual(silence.sequence, 2)
        self.assertFalse(silence.is_permanent)
        self.assertFalse(silence.is_public_flag)
        expected = before + SILENCE_DURATIONS[2]  # 30 days
        delta = abs((silence.ends_at - expected).total_seconds())
        self.assertLess(delta, 60, "second silence should end ~30 days out")
        self.assertGreater(silence.ends_at, timezone.now() + timedelta(days=29))

    def test_third_silence_is_permanent_and_publicly_flagged(self):
        for seq in (1, 2):
            Silence.objects.create(
                target=self.victim,
                issued_by=self.moderator,
                reason=f"prior {seq}",
                sequence=seq,
                starts_at=timezone.now(),
                ends_at=timezone.now() + timedelta(days=7 * seq),
            )
        silence = mod.silence(self.moderator, self.victim, reason="threat #3")
        self.assertEqual(silence.sequence, 3)
        self.assertTrue(silence.is_permanent)
        self.assertTrue(silence.is_public_flag)
        self.assertIsNone(silence.ends_at)

    def test_fourth_and_beyond_stays_permanent(self):
        for seq in (1, 2, 3):
            Silence.objects.create(
                target=self.victim,
                issued_by=self.moderator,
                reason=f"prior {seq}",
                sequence=seq,
                starts_at=timezone.now(),
                ends_at=None if seq >= 3 else timezone.now() + timedelta(days=7),
                is_permanent=seq >= 3,
                is_public_flag=seq >= 3,
            )
        silence = mod.silence(self.moderator, self.victim, reason="threat #4")
        self.assertEqual(silence.sequence, 4)
        self.assertTrue(silence.is_permanent)
        self.assertTrue(silence.is_public_flag)
        self.assertIsNone(silence.ends_at)

    def test_silence_persists_with_issuer_and_reason(self):
        silence = mod.silence(self.moderator, self.victim, reason="explicit threat")
        stored = Silence.objects.get(pk=silence.pk)
        self.assertEqual(stored.issued_by, self.moderator)
        self.assertEqual(stored.target, self.victim)
        self.assertEqual(stored.reason, "explicit threat")


class SilencePermissionTests(ModerationTestBase):
    def test_cannot_silence_a_creator(self):
        with self.assertRaises(PermissionDenied):
            mod.silence(self.moderator, self.creator, reason="nope")
        self.assertEqual(Silence.objects.count(), 0)

    def test_cannot_silence_yourself(self):
        with self.assertRaises(PermissionDenied):
            mod.silence(self.moderator, self.moderator, reason="nope")
        self.assertEqual(Silence.objects.count(), 0)

    def test_creator_cannot_silence_themselves(self):
        with self.assertRaises(PermissionDenied):
            mod.silence(self.creator, self.creator, reason="nope")

    def test_moderator_cannot_silence_another_moderator(self):
        # Silence respects the same authority rule as ban: only a Creator may
        # sanction a Community Moderator, so a peer moderator cannot.
        other_mod = make_user("mod2", is_community_moderator=True)
        with self.assertRaises(PermissionDenied):
            mod.silence(self.moderator, other_mod, reason="threats in DMs")
        self.assertEqual(Silence.objects.filter(target=other_mod).count(), 0)

    def test_creator_can_silence_a_moderator(self):
        # A Creator outranks a moderator and may silence one.
        other_mod = make_user("mod3", is_community_moderator=True)
        silence = mod.silence(self.creator, other_mod, reason="threats in DMs")
        self.assertEqual(silence.target, other_mod)

    def test_moderator_can_silence_a_founder(self):
        founder = make_user("founder", is_founder=True)
        self.assertEqual(self.fresh(founder).level, Level.FOUNDER)
        silence = mod.silence(self.moderator, founder, reason="threat")
        self.assertEqual(silence.sequence, 1)


# ---------------------------------------------------------------------------
# active_silence / can_participate
# ---------------------------------------------------------------------------
class SilenceEnforcementQueryTests(ModerationTestBase):
    def test_active_silence_blocks_participation(self):
        mod.silence(self.moderator, self.victim, reason="threat")
        victim = self.fresh(self.victim)
        self.assertIsNotNone(mod.active_silence(victim))
        self.assertFalse(mod.can_participate(victim))

    def test_expired_silence_does_not_block(self):
        Silence.objects.create(
            target=self.victim,
            issued_by=self.moderator,
            reason="old",
            sequence=1,
            starts_at=timezone.now() - timedelta(days=14),
            ends_at=timezone.now() - timedelta(days=7),
        )
        victim = self.fresh(self.victim)
        self.assertIsNone(mod.active_silence(victim))
        self.assertTrue(mod.can_participate(victim))

    def test_permanent_silence_is_always_active(self):
        Silence.objects.create(
            target=self.victim,
            issued_by=self.moderator,
            reason="permanent",
            sequence=3,
            starts_at=timezone.now() - timedelta(days=365),
            ends_at=None,
            is_permanent=True,
            is_public_flag=True,
        )
        victim = self.fresh(self.victim)
        silence = mod.active_silence(victim)
        self.assertIsNotNone(silence)
        self.assertTrue(silence.is_permanent)
        self.assertFalse(mod.can_participate(victim))

    def test_user_with_no_silences_can_participate(self):
        self.assertIsNone(mod.active_silence(self.fresh(self.victim)))
        self.assertTrue(mod.can_participate(self.fresh(self.victim)))

    def test_active_silence_none_for_anonymous(self):
        class Anon:
            is_authenticated = False

        self.assertIsNone(mod.active_silence(Anon()))

    def test_can_participate_false_for_inactive_user(self):
        self.victim.is_active = False
        self.victim.save(update_fields=["is_active"])
        self.assertFalse(mod.can_participate(self.fresh(self.victim)))


# ---------------------------------------------------------------------------
# ban / lift_ban
# ---------------------------------------------------------------------------
class BanTests(ModerationTestBase):
    def test_ban_deactivates_and_records(self):
        ban = mod.ban(self.moderator, self.victim, reason="posted illegal content")
        self.assertEqual(Ban.objects.count(), 1)
        self.assertEqual(ban.target, self.victim)
        self.assertEqual(ban.issued_by, self.moderator)
        self.assertIsNone(ban.lifted_at)
        self.assertTrue(ban.is_active)
        # Account is deactivated (is_active False).
        self.assertFalse(self.fresh(self.victim).is_active)

    def test_is_banned_true_after_ban(self):
        mod.ban(self.moderator, self.victim, reason="illegal")
        self.assertTrue(mod.is_banned(self.fresh(self.victim)))

    def test_banned_user_cannot_participate(self):
        mod.ban(self.moderator, self.victim, reason="illegal")
        self.assertFalse(mod.can_participate(self.fresh(self.victim)))

    def test_inactive_user_reads_as_banned_even_without_ban_row(self):
        # is_banned short-circuits on is_active.
        self.victim.is_active = False
        self.victim.save(update_fields=["is_active"])
        self.assertTrue(mod.is_banned(self.fresh(self.victim)))

    def test_active_user_with_no_ban_is_not_banned(self):
        self.assertFalse(mod.is_banned(self.fresh(self.victim)))

    def test_cannot_ban_a_creator(self):
        with self.assertRaises(PermissionDenied):
            mod.ban(self.moderator, self.creator, reason="nope")
        self.assertEqual(Ban.objects.count(), 0)
        self.assertTrue(self.fresh(self.creator).is_active)

    def test_creator_cannot_ban_a_creator(self):
        other_creator = make_user("creator2", is_riffhub_creator=True)
        with self.assertRaises(PermissionDenied):
            mod.ban(self.creator, other_creator, reason="nope")

    def test_cannot_ban_yourself(self):
        with self.assertRaises(PermissionDenied):
            mod.ban(self.moderator, self.moderator, reason="nope")
        self.assertEqual(Ban.objects.count(), 0)

    def test_creator_cannot_ban_themselves(self):
        with self.assertRaises(PermissionDenied):
            mod.ban(self.creator, self.creator, reason="nope")

    def test_moderator_cannot_ban_another_moderator(self):
        other_mod = make_user("mod2", is_community_moderator=True)
        with self.assertRaises(PermissionDenied):
            mod.ban(self.moderator, other_mod, reason="nope")
        self.assertEqual(Ban.objects.count(), 0)
        self.assertTrue(self.fresh(other_mod).is_active)

    def test_creator_can_ban_a_moderator(self):
        other_mod = make_user("mod2", is_community_moderator=True)
        ban = mod.ban(self.creator, other_mod, reason="abuse of tools")
        self.assertEqual(ban.target, other_mod)
        self.assertFalse(self.fresh(other_mod).is_active)

    def test_moderator_can_ban_a_regular_user(self):
        ban = mod.ban(self.moderator, self.regular, reason="illegal content")
        self.assertEqual(ban.target, self.regular)
        self.assertFalse(self.fresh(self.regular).is_active)

    def test_moderator_can_ban_a_founder(self):
        founder = make_user("founder", is_founder=True)
        ban = mod.ban(self.moderator, founder, reason="illegal content")
        self.assertEqual(ban.target, founder)
        self.assertFalse(self.fresh(founder).is_active)


class LiftBanTests(ModerationTestBase):
    def test_lift_ban_reactivates_account(self):
        mod.ban(self.moderator, self.victim, reason="illegal")
        self.assertFalse(self.fresh(self.victim).is_active)

        mod.lift_ban(self.moderator, self.victim)
        self.assertTrue(self.fresh(self.victim).is_active)

    def test_lift_ban_marks_ban_lifted(self):
        ban = mod.ban(self.moderator, self.victim, reason="illegal")
        mod.lift_ban(self.moderator, self.victim)
        ban.refresh_from_db()
        self.assertIsNotNone(ban.lifted_at)
        self.assertFalse(ban.is_active)

    def test_is_banned_false_after_lift(self):
        mod.ban(self.moderator, self.victim, reason="illegal")
        mod.lift_ban(self.moderator, self.victim)
        self.assertFalse(mod.is_banned(self.fresh(self.victim)))

    def test_lifted_user_can_participate_again(self):
        mod.ban(self.moderator, self.victim, reason="illegal")
        mod.lift_ban(self.moderator, self.victim)
        self.assertTrue(mod.can_participate(self.fresh(self.victim)))

    def test_lift_ban_only_affects_active_bans(self):
        # An already-lifted historical ban is not re-touched, the active one is.
        old = Ban.objects.create(
            target=self.victim,
            issued_by=self.moderator,
            reason="old",
            lifted_at=timezone.now() - timedelta(days=30),
        )
        original_lift = old.lifted_at
        mod.ban(self.moderator, self.victim, reason="new")
        mod.lift_ban(self.moderator, self.victim)
        old.refresh_from_db()
        # The historical ban's lifted_at is untouched.
        self.assertEqual(old.lifted_at, original_lift)
        self.assertFalse(
            Ban.objects.filter(target=self.victim, lifted_at__isnull=True).exists()
        )

    def test_lift_ban_requires_moderator(self):
        mod.ban(self.moderator, self.victim, reason="illegal")
        with self.assertRaises(PermissionDenied):
            mod.lift_ban(self.regular, self.victim)


# ---------------------------------------------------------------------------
# move_content
# ---------------------------------------------------------------------------
class MoveContentTests(ModerationTestBase):
    def setUp(self):
        super().setUp()
        self.other_subtopic = Subtopic.objects.create(
            topic=self.topic, name="Basses"
        )
        self.post = create_post(
            subtopic=self.subtopic,
            author=self.regular,
            title="My bass rig",
            body="posted under guitars by mistake",
        )

    def test_move_changes_subtopic(self):
        mod.move_content(self.moderator, self.post, self.other_subtopic)
        self.post.refresh_from_db()
        self.assertEqual(self.post.subtopic, self.other_subtopic)

    def test_move_logs_content_action(self):
        mod.move_content(
            self.moderator, self.post, self.other_subtopic, reason="wrong subtopic"
        )
        action = ContentAction.objects.get()
        self.assertEqual(action.action, ContentActionType.MOVE)
        self.assertEqual(action.moderator, self.moderator)
        self.assertEqual(action.content, self.post)
        self.assertEqual(action.reason, "wrong subtopic")

    def test_move_records_from_and_to_in_detail(self):
        from_label = str(self.post.subtopic)
        mod.move_content(self.moderator, self.post, self.other_subtopic)
        action = ContentAction.objects.get()
        self.assertEqual(action.detail["from"], from_label)
        self.assertEqual(action.detail["to"], str(self.other_subtopic))

    def test_move_requires_moderator(self):
        with self.assertRaises(PermissionDenied):
            mod.move_content(self.regular, self.post, self.other_subtopic)
        self.post.refresh_from_db()
        self.assertEqual(self.post.subtopic, self.subtopic)
        self.assertEqual(ContentAction.objects.count(), 0)

    def test_creator_can_move(self):
        mod.move_content(self.creator, self.post, self.other_subtopic)
        self.post.refresh_from_db()
        self.assertEqual(self.post.subtopic, self.other_subtopic)


# ---------------------------------------------------------------------------
# remove_content / restore_content
# ---------------------------------------------------------------------------
class RemoveRestoreContentTests(ModerationTestBase):
    def setUp(self):
        super().setUp()
        self.post = create_post(
            subtopic=self.subtopic,
            author=self.regular,
            title="Football scores",
            body="totally unrelated to guitars",
        )
        self.comment = create_comment(
            post=self.post, author=self.victim, body="me too, go team"
        )

    def test_remove_post_soft_removes(self):
        mod.remove_content(self.moderator, self.post, reason="off-topic")
        self.post.refresh_from_db()
        self.assertTrue(self.post.is_removed)
        self.assertEqual(self.post.removed_by, self.moderator)
        self.assertEqual(self.post.removal_reason, "off-topic")
        self.assertIsNotNone(self.post.removed_at)

    def test_remove_logs_content_action(self):
        mod.remove_content(self.moderator, self.post, reason="off-topic")
        action = ContentAction.objects.get()
        self.assertEqual(action.action, ContentActionType.REMOVE)
        self.assertEqual(action.content, self.post)
        self.assertEqual(action.moderator, self.moderator)
        self.assertEqual(action.reason, "off-topic")

    def test_remove_comment_soft_removes(self):
        mod.remove_content(self.moderator, self.comment, reason="spam")
        self.comment.refresh_from_db()
        self.assertTrue(self.comment.is_removed)
        self.assertEqual(self.comment.removed_by, self.moderator)
        action = ContentAction.objects.get()
        self.assertEqual(action.content, self.comment)
        self.assertEqual(
            action.content_type, ContentType.objects.get_for_model(Comment)
        )

    def test_restore_content_restores(self):
        mod.remove_content(self.moderator, self.post, reason="off-topic")
        mod.restore_content(self.moderator, self.post)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_removed)
        self.assertIsNone(self.post.removed_at)
        self.assertIsNone(self.post.removed_by)
        self.assertEqual(self.post.removal_reason, "")

    def test_restore_logs_content_action(self):
        mod.remove_content(self.moderator, self.post, reason="off-topic")
        mod.restore_content(self.moderator, self.post)
        actions = ContentAction.objects.filter(action=ContentActionType.RESTORE)
        self.assertEqual(actions.count(), 1)
        self.assertEqual(actions.get().content, self.post)

    def test_remove_then_restore_leaves_two_audit_rows(self):
        mod.remove_content(self.moderator, self.post, reason="off-topic")
        mod.restore_content(self.moderator, self.post)
        self.assertEqual(ContentAction.objects.count(), 2)

    def test_remove_requires_moderator(self):
        with self.assertRaises(PermissionDenied):
            mod.remove_content(self.regular, self.post, reason="nope")
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_removed)
        self.assertEqual(ContentAction.objects.count(), 0)

    def test_restore_requires_moderator(self):
        mod.remove_content(self.moderator, self.post, reason="off-topic")
        with self.assertRaises(PermissionDenied):
            mod.restore_content(self.regular, self.post)
        self.post.refresh_from_db()
        self.assertTrue(self.post.is_removed)


# ---------------------------------------------------------------------------
# enforcement seam — silenced/banned users blocked from create_post/comment
# ---------------------------------------------------------------------------
class EnforcementTests(ModerationTestBase):
    def test_silenced_user_cannot_create_post(self):
        mod.silence(self.moderator, self.victim, reason="threat")
        victim = self.fresh(self.victim)
        with self.assertRaises(PermissionDenied):
            create_post(
                subtopic=self.subtopic,
                author=victim,
                title="Should fail",
                body="silenced",
            )
        self.assertEqual(Post.objects.count(), 0)

    def test_silenced_user_cannot_create_comment(self):
        post = create_post(
            subtopic=self.subtopic,
            author=self.regular,
            title="Open thread",
            body="anyone home",
        )
        mod.silence(self.moderator, self.victim, reason="threat")
        victim = self.fresh(self.victim)
        with self.assertRaises(PermissionDenied):
            create_comment(post=post, author=victim, body="silenced reply")
        self.assertEqual(Comment.objects.count(), 0)

    def test_permanently_silenced_user_cannot_post(self):
        Silence.objects.create(
            target=self.victim,
            issued_by=self.moderator,
            reason="permanent",
            sequence=3,
            starts_at=timezone.now(),
            ends_at=None,
            is_permanent=True,
            is_public_flag=True,
        )
        victim = self.fresh(self.victim)
        with self.assertRaises(PermissionDenied):
            create_post(
                subtopic=self.subtopic, author=victim, title="x", body="y"
            )

    def test_banned_user_cannot_create_post(self):
        mod.ban(self.moderator, self.victim, reason="illegal")
        victim = self.fresh(self.victim)
        with self.assertRaises(PermissionDenied):
            create_post(
                subtopic=self.subtopic,
                author=victim,
                title="Should fail",
                body="banned",
            )
        self.assertEqual(Post.objects.count(), 0)

    def test_banned_user_cannot_create_comment(self):
        post = create_post(
            subtopic=self.subtopic,
            author=self.regular,
            title="Open thread",
            body="hello",
        )
        mod.ban(self.moderator, self.victim, reason="illegal")
        victim = self.fresh(self.victim)
        with self.assertRaises(PermissionDenied):
            create_comment(post=post, author=victim, body="banned reply")

    def test_expired_silence_lets_user_post_again(self):
        Silence.objects.create(
            target=self.victim,
            issued_by=self.moderator,
            reason="old",
            sequence=1,
            starts_at=timezone.now() - timedelta(days=14),
            ends_at=timezone.now() - timedelta(days=7),
        )
        victim = self.fresh(self.victim)
        post = create_post(
            subtopic=self.subtopic,
            author=victim,
            title="Back at it",
            body="silence expired",
        )
        self.assertEqual(Post.objects.count(), 1)
        self.assertEqual(post.author, victim)

    def test_lifted_ban_lets_user_post_again(self):
        mod.ban(self.moderator, self.victim, reason="illegal")
        mod.lift_ban(self.moderator, self.victim)
        victim = self.fresh(self.victim)
        post = create_post(
            subtopic=self.subtopic,
            author=victim,
            title="Reinstated",
            body="ban lifted",
        )
        self.assertEqual(post.author, victim)

    def test_unsanctioned_user_can_post_and_comment(self):
        post = create_post(
            subtopic=self.subtopic,
            author=self.regular,
            title="Clean post",
            body="no sanctions",
        )
        comment = create_comment(post=post, author=self.victim, body="clean reply")
        self.assertEqual(Post.objects.count(), 1)
        self.assertEqual(Comment.objects.count(), 1)
        self.assertEqual(comment.post, post)
