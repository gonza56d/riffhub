"""Tests for the collab-db review / voting UI (``catalog.views_review``).

Covers the PRODUCT.md rules the review UI implements, at the HTTP layer
(``django.test.Client``):

* ``/review/`` lists every UNDER_REVISION entry and is open to *any* logged-in
  user (transparency), while anonymous users are bounced to login.
* ``/review/<kind>/<pk>/`` renders a submission's proposed fields.
* ``/review/<kind>/<pk>/vote/<up|down>/`` — only Database Collaborators (and
  above) may vote (non-collaborator => 403); a successful vote returns the
  re-rendered vote widget; once the configured bar (net >= 3 votes, >= 3
  distinct voters) is cleared the entry auto-publishes and shows up in the
  public browse at ``/``.
* ``/review/<kind>/<pk>/correct/`` — a collaborator files a ``Correction``; a
  non-collaborator gets 403 and nothing is created.

Conventions: per-test DB + rollback (``TestCase``), ``force_login`` (no CSRF,
no password). The collaborator-promotion threshold is set in ``setUp`` so the
config-driven Collaborator derivation is exercised the way PRODUCT.md intends
(no silent default).
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from accounts.models import Level
from catalog.constants import CorrectionStatus, PublicationStatus, VoteValue
from catalog.models import (
    Brand,
    BridgeType,
    Bridge,
    Correction,
    GuitarModel,
    NutMaterial,
    Nut,
    PickupType,
    Pickup,
    ReviewVote,
    Tuner,
)
from catalog.services import cast_review_vote
from core.models import SiteConfiguration

User = get_user_model()

# Collaborator promotion threshold used across the suite. Small so we can mint
# collaborators by hand-setting accepted_submissions_count without seeding a
# whole catalog (recompute_standing only re-derives the *submitter's* count, so
# voters' hand-set counts are safe).
COLLAB_THRESHOLD = 1


class ReviewUITestBase(TestCase):
    """Shared fixtures + helpers for the review-UI tests."""

    def setUp(self):
        # PRODUCT.md: the collaborator threshold must be explicitly configured.
        self.config = SiteConfiguration.get_solo()
        self.config.collaborator_promotion_threshold = COLLAB_THRESHOLD
        # Keep the acceptance bar at the documented defaults (net >= 3, >= 3
        # distinct voters) but set them explicitly so the test is robust to
        # default changes.
        self.config.gear_acceptance_min_net_votes = 3
        self.config.gear_acceptance_min_voters = 3
        self.config.save()

        # The person who submitted the entry under review. Confirmed e-mail so
        # they are a legitimate submitter; left at Regular level.
        self.submitter = self._make_user("submitter", email_confirmed=True)

        # A plain logged-in Regular user (email confirmed but not a collaborator).
        self.regular = self._make_user("regular", email_confirmed=True)

        # Three Database Collaborators (distinct voters needed to clear the bar).
        self.collab1 = self._make_collaborator("collab1")
        self.collab2 = self._make_collaborator("collab2")
        self.collab3 = self._make_collaborator("collab3")

    # --- user factories ---------------------------------------------------
    def _make_user(self, username, **kwargs):
        defaults = {
            "email": f"{username}@example.com",
            "password": "irrelevant-for-force-login",
        }
        flags = {}
        for key in (
            "email_confirmed",
            "is_community_moderator",
            "is_riffhub_creator",
            "is_founder",
            "accepted_submissions_count",
        ):
            if key in kwargs:
                flags[key] = kwargs.pop(key)
        defaults.update(kwargs)
        user = User.objects.create_user(username, **defaults)
        if flags:
            for key, value in flags.items():
                setattr(user, key, value)
            user.save()
        return user

    def _make_collaborator(self, username):
        """A user the config-driven derivation resolves to COLLABORATOR."""
        user = self._make_user(
            username,
            email_confirmed=True,
            accepted_submissions_count=COLLAB_THRESHOLD,
        )
        # Sanity: the derivation really does promote them to Collaborator.
        assert user.is_at_least(Level.COLLABORATOR), "fixture must be a collaborator"
        return user

    # --- catalog factories ------------------------------------------------
    def _make_brand(self, name="Ibanez", status=PublicationStatus.UNDER_REVISION,
                    submitted_by=None):
        return Brand.objects.create(
            name=name,
            status=status,
            submitted_by=submitted_by if submitted_by is not None else self.submitter,
        )

    def _make_guitar(self, name="RG Test", *, brand=None,
                     status=PublicationStatus.UNDER_REVISION, submitted_by=None,
                     num_strings=6):
        if brand is None:
            brand = self._make_brand(
                name=f"Brand for {name}", status=PublicationStatus.PUBLISHED
            )
        return GuitarModel.objects.create(
            brand=brand,
            name=name,
            num_strings=num_strings,
            scale_length_min_inches=Decimal("25.500"),
            scale_length_max_inches=Decimal("25.500"),
            status=status,
            submitted_by=submitted_by if submitted_by is not None else self.submitter,
        )

    # --- URL helpers ------------------------------------------------------
    def _detail_url(self, kind, obj):
        return reverse("catalog:review_detail", args=[kind, obj.pk])

    def _vote_url(self, kind, obj, value):
        return reverse("catalog:review_vote", args=[kind, obj.pk, value])

    def _correct_url(self, kind, obj):
        return reverse("catalog:review_correct", args=[kind, obj.pk])


# ---------------------------------------------------------------------------
# Queue: /review/
# ---------------------------------------------------------------------------
class ReviewQueueTests(ReviewUITestBase):
    def test_queue_requires_login(self):
        # @login_required -> anonymous is redirected to the login page.
        resp = self.client.get(reverse("catalog:review_queue"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_queue_open_to_regular_user(self):
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("catalog:review_queue"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "catalog/review/queue.html")

    def test_queue_lists_under_revision_entries(self):
        guitar = self._make_guitar("Under-Revision RG")
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("catalog:review_queue"))
        rows = resp.context["rows"]
        self.assertIn(guitar.pk, [r["obj"].pk for r in rows if r["kind"] == "guitar"])
        self.assertContains(resp, "Under-Revision RG")

    def test_queue_excludes_published_entries(self):
        published = self._make_guitar(
            "Already Published", status=PublicationStatus.PUBLISHED
        )
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("catalog:review_queue"))
        guitar_pks = [r["obj"].pk for r in resp.context["rows"] if r["kind"] == "guitar"]
        self.assertNotIn(published.pk, guitar_pks)

    def test_queue_excludes_rejected_entries(self):
        rejected = self._make_guitar(
            "Rejected One", status=PublicationStatus.REJECTED
        )
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("catalog:review_queue"))
        guitar_pks = [r["obj"].pk for r in resp.context["rows"] if r["kind"] == "guitar"]
        self.assertNotIn(rejected.pk, guitar_pks)

    def test_queue_merges_multiple_kinds(self):
        # One of each of a few kinds, all under revision.
        self._make_guitar("Queue Guitar")
        self._make_brand("Queue Brand")
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("catalog:review_queue"))
        kinds = {r["kind"] for r in resp.context["rows"]}
        self.assertIn("guitar", kinds)
        self.assertIn("brand", kinds)

    def test_queue_sorted_newest_first(self):
        older = self._make_guitar("Older")
        newer = self._make_guitar("Newer")
        # Force a deterministic ordering on created_at.
        GuitarModel.objects.filter(pk=older.pk).update(
            created_at=newer.created_at - timedelta(days=1)
        )
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("catalog:review_queue"))
        guitar_rows = [r for r in resp.context["rows"] if r["kind"] == "guitar"]
        self.assertEqual(guitar_rows[0]["obj"].pk, newer.pk)
        self.assertEqual(guitar_rows[-1]["obj"].pk, older.pk)

    def test_queue_empty_state(self):
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("catalog:review_queue"))
        self.assertEqual(list(resp.context["rows"]), [])
        self.assertContains(resp, "Nothing to review")

    def test_queue_can_review_false_for_regular(self):
        self._make_guitar("Some Guitar")
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("catalog:review_queue"))
        self.assertFalse(resp.context["can_review"])
        # Regular users see the "become a collaborator" notice.
        self.assertContains(resp, "Database Collaborator")

    def test_queue_can_review_true_for_collaborator(self):
        self._make_guitar("Some Guitar")
        self.client.force_login(self.collab1)
        resp = self.client.get(reverse("catalog:review_queue"))
        self.assertTrue(resp.context["can_review"])

    def test_queue_net_votes_surfaced(self):
        guitar = self._make_guitar("Voted Guitar")
        cast_review_vote(self.collab1, guitar, VoteValue.UP)
        cast_review_vote(self.collab2, guitar, VoteValue.UP)
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("catalog:review_queue"))
        row = next(r for r in resp.context["rows"] if r["obj"].pk == guitar.pk)
        self.assertEqual(row["net"], 2)


# ---------------------------------------------------------------------------
# Detail: /review/<kind>/<pk>/
# ---------------------------------------------------------------------------
class ReviewDetailTests(ReviewUITestBase):
    def test_detail_requires_login(self):
        guitar = self._make_guitar()
        resp = self.client.get(self._detail_url("guitar", guitar))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_detail_renders_for_regular_user(self):
        guitar = self._make_guitar("Detail RG")
        self.client.force_login(self.regular)
        resp = self.client.get(self._detail_url("guitar", guitar))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "catalog/review/detail.html")
        self.assertContains(resp, "Detail RG")

    def test_detail_renders_proposed_guitar_fields(self):
        brand = self._make_brand("DetailBrand", status=PublicationStatus.PUBLISHED)
        guitar = self._make_guitar("Spec RG", brand=brand, num_strings=7)
        self.client.force_login(self.regular)
        resp = self.client.get(self._detail_url("guitar", guitar))
        self.assertEqual(resp.context["kind"], "guitar")
        self.assertEqual(resp.context["kind_label"], "Guitar")
        self.assertEqual(resp.context["obj"].pk, guitar.pk)
        # The spec sheet renders the hand-entered fields.
        self.assertContains(resp, "7-string")
        self.assertContains(resp, "DetailBrand")

    def test_detail_renders_brand_kind(self):
        brand = self._make_brand("Lonely Brand")
        self.client.force_login(self.regular)
        resp = self.client.get(self._detail_url("brand", brand))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["kind_label"], "Brand")
        self.assertContains(resp, "Lonely Brand")

    def test_detail_renders_gear_kinds(self):
        brand = self._make_brand("GearBrand", status=PublicationStatus.PUBLISHED)
        bt = BridgeType.objects.create(name="Hardtail")
        bridge = Bridge.objects.create(
            brand=brand, name="HT-1", bridge_type=bt,
            status=PublicationStatus.UNDER_REVISION, submitted_by=self.submitter,
        )
        nut_mat = NutMaterial.objects.create(name="Bone")
        nut = Nut.objects.create(
            brand=brand, name="Bone Nut", material=nut_mat,
            status=PublicationStatus.UNDER_REVISION, submitted_by=self.submitter,
        )
        pt = PickupType.objects.create(name="Humbucker", symbol="H", is_humbucking=True)
        pickup = Pickup.objects.create(
            brand=brand, name="H-100", pickup_type=pt,
            status=PublicationStatus.UNDER_REVISION, submitted_by=self.submitter,
        )
        tuner = Tuner.objects.create(
            brand=brand, name="Locker", is_locking=True,
            status=PublicationStatus.UNDER_REVISION, submitted_by=self.submitter,
        )
        self.client.force_login(self.regular)
        for kind, obj, needle in (
            ("bridge", bridge, "HT-1"),
            ("nut", nut, "Bone Nut"),
            ("pickup", pickup, "H-100"),
            ("tuner", tuner, "Locker"),
        ):
            resp = self.client.get(self._detail_url(kind, obj))
            self.assertEqual(resp.status_code, 200, kind)
            self.assertContains(resp, needle)

    def test_detail_404_unknown_kind(self):
        guitar = self._make_guitar()
        self.client.force_login(self.regular)
        resp = self.client.get(
            reverse("catalog:review_detail", args=["widget", guitar.pk])
        )
        self.assertEqual(resp.status_code, 404)

    def test_detail_404_unknown_pk(self):
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("catalog:review_detail", args=["guitar", 999999]))
        self.assertEqual(resp.status_code, 404)

    def test_detail_can_review_flag(self):
        guitar = self._make_guitar()
        # Regular user: cannot review.
        self.client.force_login(self.regular)
        resp = self.client.get(self._detail_url("guitar", guitar))
        self.assertFalse(resp.context["can_review"])
        # Collaborator: can review.
        self.client.force_login(self.collab1)
        resp = self.client.get(self._detail_url("guitar", guitar))
        self.assertTrue(resp.context["can_review"])

    def test_detail_vote_widget_disabled_for_submitter(self):
        # A submitter cannot vote on their own entry — but make the submitter a
        # collaborator so it's the self-vote rule (not the level rule) at play.
        self.submitter.accepted_submissions_count = COLLAB_THRESHOLD
        self.submitter.save()
        guitar = self._make_guitar(submitted_by=self.submitter)
        self.client.force_login(self.submitter)
        resp = self.client.get(self._detail_url("guitar", guitar))
        self.assertFalse(resp.context["vote"]["can_vote"])

    def test_detail_vote_widget_enabled_for_other_collaborator(self):
        guitar = self._make_guitar(submitted_by=self.submitter)
        self.client.force_login(self.collab1)
        resp = self.client.get(self._detail_url("guitar", guitar))
        self.assertTrue(resp.context["vote"]["can_vote"])

    def test_detail_shows_existing_corrections(self):
        guitar = self._make_guitar()
        ct = ContentType.objects.get_for_model(guitar)
        Correction.objects.create(
            author=self.collab1, content_type=ct, object_id=guitar.pk,
            body="Scale length is wrong.", status=CorrectionStatus.OPEN,
        )
        self.client.force_login(self.regular)
        resp = self.client.get(self._detail_url("guitar", guitar))
        self.assertContains(resp, "Scale length is wrong.")


# ---------------------------------------------------------------------------
# Voting: /review/<kind>/<pk>/vote/<up|down>/
# ---------------------------------------------------------------------------
class ReviewVoteTests(ReviewUITestBase):
    def test_vote_anonymous_forbidden(self):
        # The vote endpoint is not @login_required; _can_review short-circuits
        # an anonymous user to a 403 (NOT a login redirect).
        guitar = self._make_guitar()
        resp = self.client.post(self._vote_url("guitar", guitar, "up"))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(ReviewVote.objects.count(), 0)

    def test_vote_regular_user_forbidden(self):
        guitar = self._make_guitar()
        self.client.force_login(self.regular)
        resp = self.client.post(self._vote_url("guitar", guitar, "up"))
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b"Collaborators", resp.content)
        self.assertEqual(ReviewVote.objects.count(), 0)

    def test_vote_requires_post(self):
        guitar = self._make_guitar()
        self.client.force_login(self.collab1)
        resp = self.client.get(self._vote_url("guitar", guitar, "up"))
        self.assertEqual(resp.status_code, 405)

    def test_collaborator_upvote_returns_widget(self):
        guitar = self._make_guitar()
        self.client.force_login(self.collab1)
        resp = self.client.post(self._vote_url("guitar", guitar, "up"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "catalog/review/_review_vote.html")
        # A +1 was recorded for this collaborator on this target.
        ct = ContentType.objects.get_for_model(guitar)
        vote = ReviewVote.objects.get(
            voter=self.collab1, content_type=ct, object_id=guitar.pk
        )
        self.assertEqual(vote.value, VoteValue.UP)
        # The widget reflects the new tally.
        self.assertEqual(resp.context["v"]["net"], 1)
        self.assertEqual(resp.context["v"]["voters"], 1)

    def test_collaborator_downvote_records_negative(self):
        guitar = self._make_guitar()
        self.client.force_login(self.collab1)
        resp = self.client.post(self._vote_url("guitar", guitar, "down"))
        self.assertEqual(resp.status_code, 200)
        ct = ContentType.objects.get_for_model(guitar)
        vote = ReviewVote.objects.get(
            voter=self.collab1, content_type=ct, object_id=guitar.pk
        )
        self.assertEqual(vote.value, VoteValue.DOWN)
        self.assertEqual(resp.context["v"]["net"], -1)

    def test_vote_value_other_than_up_is_treated_as_down(self):
        # The view maps "up" -> UP and anything else -> DOWN.
        guitar = self._make_guitar()
        self.client.force_login(self.collab1)
        resp = self.client.post(self._vote_url("guitar", guitar, "down"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["v"]["down_active"], True)
        self.assertEqual(resp.context["v"]["up_active"], False)

    def test_recasting_same_vote_toggles_off(self):
        guitar = self._make_guitar()
        self.client.force_login(self.collab1)
        self.client.post(self._vote_url("guitar", guitar, "up"))
        resp = self.client.post(self._vote_url("guitar", guitar, "up"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ReviewVote.objects.count(), 0)
        self.assertEqual(resp.context["v"]["net"], 0)

    def test_switching_vote_direction(self):
        guitar = self._make_guitar()
        self.client.force_login(self.collab1)
        self.client.post(self._vote_url("guitar", guitar, "up"))
        resp = self.client.post(self._vote_url("guitar", guitar, "down"))
        self.assertEqual(ReviewVote.objects.count(), 1)
        self.assertEqual(resp.context["v"]["net"], -1)
        self.assertTrue(resp.context["v"]["down_active"])

    def test_collaborator_cannot_vote_own_submission(self):
        # Submitter is also a collaborator; self-vote must 403 via the service's
        # PermissionError, and no vote is recorded.
        self.submitter.accepted_submissions_count = COLLAB_THRESHOLD
        self.submitter.save()
        guitar = self._make_guitar(submitted_by=self.submitter)
        self.client.force_login(self.submitter)
        resp = self.client.post(self._vote_url("guitar", guitar, "up"))
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b"your own submission", resp.content)
        self.assertEqual(ReviewVote.objects.count(), 0)

    def test_vote_unknown_kind_404(self):
        guitar = self._make_guitar()
        self.client.force_login(self.collab1)
        resp = self.client.post(
            reverse("catalog:review_vote", args=["widget", guitar.pk, "up"])
        )
        self.assertEqual(resp.status_code, 404)

    def test_vote_unknown_pk_404(self):
        self.client.force_login(self.collab1)
        resp = self.client.post(
            reverse("catalog:review_vote", args=["guitar", 999999, "up"])
        )
        self.assertEqual(resp.status_code, 404)

    # --- auto-publish on reaching the bar --------------------------------
    def test_reaching_bar_auto_publishes(self):
        guitar = self._make_guitar("Auto Publish Me", submitted_by=self.submitter)
        self.assertEqual(guitar.status, PublicationStatus.UNDER_REVISION)

        # Two votes: still under revision (below the 3-voter / 3-net bar).
        self.client.force_login(self.collab1)
        self.client.post(self._vote_url("guitar", guitar, "up"))
        self.client.force_login(self.collab2)
        resp = self.client.post(self._vote_url("guitar", guitar, "up"))
        guitar.refresh_from_db()
        self.assertEqual(guitar.status, PublicationStatus.UNDER_REVISION)
        self.assertFalse(resp.context["v"]["just_published"])

        # Third collaborator's vote clears net>=3 AND voters>=3 -> publish.
        self.client.force_login(self.collab3)
        resp = self.client.post(self._vote_url("guitar", guitar, "up"))
        guitar.refresh_from_db()
        self.assertEqual(guitar.status, PublicationStatus.PUBLISHED)
        self.assertIsNotNone(guitar.published_at)
        # The widget flags the fresh publication.
        self.assertTrue(resp.context["v"]["just_published"])
        self.assertTrue(resp.context["v"]["is_published"])

    def test_published_entry_appears_in_public_browse(self):
        # A guitar with single (non-multiscale) scale so a plain browse lists it.
        guitar = self._make_guitar("Browsable RG", submitted_by=self.submitter)

        # Before publication it is NOT in the public browse.
        self.client.force_login(self.regular)
        resp = self.client.get(reverse("catalog:browse"))
        self.assertNotIn(
            guitar.pk, [g.pk for g in resp.context["guitars"]]
        )

        # Three collaborator up-votes auto-publish it.
        for collab in (self.collab1, self.collab2, self.collab3):
            self.client.force_login(collab)
            self.client.post(self._vote_url("guitar", guitar, "up"))
        guitar.refresh_from_db()
        self.assertEqual(guitar.status, PublicationStatus.PUBLISHED)

        # Now it shows up in the public browse for anyone (anonymous too).
        self.client.logout()
        resp = self.client.get(reverse("catalog:browse"))
        self.assertIn(guitar.pk, [g.pk for g in resp.context["guitars"]])
        self.assertContains(resp, "Browsable RG")

        # ...and its detail page becomes reachable (was 404 while under revision).
        resp = self.client.get(reverse("catalog:detail", args=[guitar.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_published_brand_credits_submitter_reputation(self):
        # Accepting a submission credits the submitter +1 accepted and rep.
        # Use a brand so recompute_standing counts exactly this one accepted
        # entry (no other catalog rows for the submitter).
        brand = self._make_brand("Credit Brand", submitted_by=self.submitter)
        rep_before = self.submitter.reputation_score
        for collab in (self.collab1, self.collab2, self.collab3):
            self.client.force_login(collab)
            self.client.post(self._vote_url("brand", brand, "up"))
        brand.refresh_from_db()
        self.assertEqual(brand.status, PublicationStatus.PUBLISHED)

        self.submitter.refresh_from_db()
        # recompute_standing re-derives the count from the catalog: exactly 1
        # accepted submission now exists for this user.
        self.assertEqual(self.submitter.accepted_submissions_count, 1)
        # +10 reputation for the accepted contribution (REP_ACCEPTED_SUBMISSION).
        self.assertEqual(self.submitter.reputation_score, rep_before + 10)

    def test_net_below_bar_with_enough_voters_does_not_publish(self):
        # 3 distinct voters but net only +1 (2 up, 1 down) -> stays under revision.
        guitar = self._make_guitar("Mixed Votes", submitted_by=self.submitter)
        self.client.force_login(self.collab1)
        self.client.post(self._vote_url("guitar", guitar, "up"))
        self.client.force_login(self.collab2)
        self.client.post(self._vote_url("guitar", guitar, "up"))
        self.client.force_login(self.collab3)
        self.client.post(self._vote_url("guitar", guitar, "down"))
        guitar.refresh_from_db()
        self.assertEqual(guitar.status, PublicationStatus.UNDER_REVISION)
        self.assertEqual(ReviewVote.net_votes(guitar), 1)
        self.assertEqual(ReviewVote.voter_count(guitar), 3)


# ---------------------------------------------------------------------------
# Corrections: /review/<kind>/<pk>/correct/
# ---------------------------------------------------------------------------
class ReviewCorrectionTests(ReviewUITestBase):
    def test_correct_anonymous_forbidden(self):
        guitar = self._make_guitar()
        resp = self.client.post(
            self._correct_url("guitar", guitar), {"body": "Fix this."}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Correction.objects.count(), 0)

    def test_correct_regular_user_forbidden(self):
        guitar = self._make_guitar()
        self.client.force_login(self.regular)
        resp = self.client.post(
            self._correct_url("guitar", guitar), {"body": "Fix this."}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b"Collaborators", resp.content)
        self.assertEqual(Correction.objects.count(), 0)

    def test_correct_requires_post(self):
        guitar = self._make_guitar()
        self.client.force_login(self.collab1)
        resp = self.client.get(self._correct_url("guitar", guitar))
        self.assertEqual(resp.status_code, 405)

    def test_collaborator_creates_correction(self):
        guitar = self._make_guitar()
        self.client.force_login(self.collab1)
        resp = self.client.post(
            self._correct_url("guitar", guitar),
            {"body": "Scale length should be 25.5, not 24.75."},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "catalog/review/_corrections.html")

        ct = ContentType.objects.get_for_model(guitar)
        correction = Correction.objects.get(content_type=ct, object_id=guitar.pk)
        self.assertEqual(correction.body, "Scale length should be 25.5, not 24.75.")
        # The view sets author server-side and opens the correction.
        self.assertEqual(correction.author, self.collab1)
        self.assertEqual(correction.status, CorrectionStatus.OPEN)
        self.assertIsNone(correction.resolved_by)
        # The re-rendered list contains the new correction.
        self.assertContains(resp, "Scale length should be 25.5, not 24.75.")

    def test_correction_target_generic_relation_points_at_entry(self):
        brand = self._make_brand("Correctable Brand")
        self.client.force_login(self.collab2)
        self.client.post(self._correct_url("brand", brand), {"body": "Wrong country."})
        correction = Correction.objects.get()
        self.assertEqual(correction.target, brand)
        self.assertEqual(correction.author, self.collab2)

    def test_blank_body_creates_no_correction(self):
        # CorrectionForm requires a body; an empty submission is invalid and
        # must not create a Correction (the view only saves a valid form).
        guitar = self._make_guitar()
        self.client.force_login(self.collab1)
        resp = self.client.post(self._correct_url("guitar", guitar), {"body": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Correction.objects.count(), 0)

    def test_correct_unknown_kind_404(self):
        guitar = self._make_guitar()
        self.client.force_login(self.collab1)
        resp = self.client.post(
            reverse("catalog:review_correct", args=["widget", guitar.pk]),
            {"body": "x"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_correct_unknown_pk_404(self):
        self.client.force_login(self.collab1)
        resp = self.client.post(
            reverse("catalog:review_correct", args=["guitar", 999999]),
            {"body": "x"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_multiple_corrections_listed_newest_first(self):
        guitar = self._make_guitar()
        ct = ContentType.objects.get_for_model(guitar)
        self.client.force_login(self.collab1)
        self.client.post(self._correct_url("guitar", guitar), {"body": "First fix."})
        self.client.force_login(self.collab2)
        resp = self.client.post(
            self._correct_url("guitar", guitar), {"body": "Second fix."}
        )
        corrections = list(resp.context["c"]["corrections"])
        # Correction.Meta.ordering = ["-created_at"] -> newest first.
        self.assertEqual(corrections[0].body, "Second fix.")
        self.assertEqual(corrections[-1].body, "First fix.")
        self.assertEqual(
            Correction.objects.filter(content_type=ct, object_id=guitar.pk).count(), 2
        )


# ---------------------------------------------------------------------------
# Higher-level roles (Founder / Moderator / Creator) can review too
# ---------------------------------------------------------------------------
class ReviewHigherLevelRolesTests(ReviewUITestBase):
    def test_founder_can_vote(self):
        founder = self._make_user("founder", email_confirmed=True, is_founder=True)
        self.assertEqual(founder.level, Level.FOUNDER)
        guitar = self._make_guitar(submitted_by=self.submitter)
        self.client.force_login(founder)
        resp = self.client.post(self._vote_url("guitar", guitar, "up"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ReviewVote.objects.count(), 1)

    def test_moderator_can_correct(self):
        mod = self._make_user(
            "mod", email_confirmed=True, is_community_moderator=True
        )
        self.assertEqual(mod.level, Level.MODERATOR)
        guitar = self._make_guitar(submitted_by=self.submitter)
        self.client.force_login(mod)
        resp = self.client.post(
            self._correct_url("guitar", guitar), {"body": "Moderator note."}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Correction.objects.count(), 1)

    def test_creator_can_vote(self):
        creator = self._make_user(
            "creator", email_confirmed=True, is_riffhub_creator=True
        )
        self.assertEqual(creator.level, Level.CREATOR)
        guitar = self._make_guitar(submitted_by=self.submitter)
        self.client.force_login(creator)
        resp = self.client.post(self._vote_url("guitar", guitar, "down"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ReviewVote.objects.count(), 1)
