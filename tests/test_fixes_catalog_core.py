"""Regression tests for the "catalog-core" bug-fix group.

Covers four confirmed bugs:

* ``evaluate_submission`` must be a no-op on anything that is not still
  ``UNDER_REVISION`` — re-running it on an already-PUBLISHED entry must not
  re-award reputation or move ``published_at`` (no reputation farming), and
  running it on a moderator-REJECTED entry must not resurrect it (#4/#5/#15/#32).
* ``GuitarModel`` scale lengths must be positive and the range non-inverted:
  a negative/zero scale and a min > max range must fail validation through the
  submit form (#14/#31).
* ``GuitarForm`` must only offer already-PUBLISHED brands/components so an
  under-revision dependency cannot be smuggled in at creation time (#28).

Service-level tests build ``Brand`` rows directly (the simplest CatalogEntry).
Form/view tests drive ``django.test.Client`` against ``/submit/guitar/`` with
minimal reference vocab, mirroring ``tests/test_catalog_submit_ui.py``.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse

from accounts.models import Level
from catalog.constants import (
    REP_ACCEPTED_SUBMISSION,
    PublicationStatus,
    VoteValue,
)
from catalog.forms_submit import GuitarForm
from catalog.models import Brand, Bridge, BridgeType, Country, GuitarModel
from catalog.services import cast_review_vote, evaluate_submission, reject_submission
from core.models import SiteConfiguration

User = get_user_model()


def _make_user(username, *, email_confirmed=True, accepted=0, **extra):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="pw-12345",
        email_confirmed=email_confirmed,
        **extra,
    )
    if accepted:
        user.accepted_submissions_count = accepted
        user.save(update_fields=["accepted_submissions_count"])
    return user


# ---------------------------------------------------------------------------
# evaluate_submission only acts on UNDER_REVISION entries (#4/#5/#15/#32)
# ---------------------------------------------------------------------------
class EvaluateSubmissionStatusGuardTests(TestCase):
    """Re-evaluating a finalised entry is a no-op: no re-publish, no re-credit,
    no resurrection of a rejected entry."""

    MIN_NET = 3
    MIN_VOTERS = 3

    def setUp(self):
        self.config = SiteConfiguration.get_solo()
        # threshold=3 keeps a fresh submitter Regular; founder far away.
        self.config.collaborator_promotion_threshold = 3
        self.config.founder_threshold = 30
        self.config.founder_level_achievable = True
        self.config.gear_acceptance_min_net_votes = self.MIN_NET
        self.config.gear_acceptance_min_voters = self.MIN_VOTERS
        self.config.save()

        self.submitter = _make_user("guard_submitter", email_confirmed=True)
        # A pool of Collaborators allowed to vote (1 accepted promotes them).
        self.collab1 = _make_user("guard_collab1", accepted=3)
        self.collab2 = _make_user("guard_collab2", accepted=3)
        self.collab3 = _make_user("guard_collab3", accepted=3)
        for c in (self.collab1, self.collab2, self.collab3):
            assert c.is_at_least(Level.COLLABORATOR), c.level

        self.brand = Brand.objects.create(
            name="Guarded Brand",
            submitted_by=self.submitter,
            status=PublicationStatus.UNDER_REVISION,
        )

    def _three_upvotes(self, target):
        cast_review_vote(self.collab1, target, VoteValue.UP)
        cast_review_vote(self.collab2, target, VoteValue.UP)
        cast_review_vote(self.collab3, target, VoteValue.UP)

    def test_reevaluating_published_entry_is_noop(self):
        # First evaluation publishes and credits once.
        self._three_upvotes(self.brand)
        self.assertTrue(evaluate_submission(self.brand))
        self.brand.refresh_from_db()
        self.submitter.refresh_from_db()
        self.assertEqual(self.brand.status, PublicationStatus.PUBLISHED)
        rep_after_first = self.submitter.reputation_score
        published_at_first = self.brand.published_at

        # A late/extra vote re-runs the evaluator on the PUBLISHED entry.
        self.assertFalse(evaluate_submission(self.brand))
        self.brand.refresh_from_db()
        self.submitter.refresh_from_db()
        # Reputation NOT re-awarded (would have stacked +10 on the old bug).
        self.assertEqual(self.submitter.reputation_score, rep_after_first)
        # published_at unchanged — it was not re-published.
        self.assertEqual(self.brand.published_at, published_at_first)
        self.assertEqual(self.brand.status, PublicationStatus.PUBLISHED)

    def test_reevaluating_published_does_not_increment_accepted_count(self):
        self._three_upvotes(self.brand)
        self.assertTrue(evaluate_submission(self.brand))
        self.submitter.refresh_from_db()
        count_after_first = self.submitter.accepted_submissions_count
        self.assertFalse(evaluate_submission(self.brand))
        self.submitter.refresh_from_db()
        self.assertEqual(
            self.submitter.accepted_submissions_count, count_after_first
        )

    def test_rejected_entry_is_not_resurrected_by_upvotes(self):
        # A moderator rejects the entry first.
        reject_submission(self.brand)
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.status, PublicationStatus.REJECTED)
        rep_before = self.submitter.reputation_score

        # Even an avalanche of upvotes must not override the rejection.
        self._three_upvotes(self.brand)
        self.assertFalse(evaluate_submission(self.brand))
        self.brand.refresh_from_db()
        self.submitter.refresh_from_db()
        self.assertEqual(self.brand.status, PublicationStatus.REJECTED)
        self.assertIsNone(self.brand.published_at)
        # No acceptance reputation awarded for a rejected entry.
        self.assertEqual(self.submitter.reputation_score, rep_before)

    def test_under_revision_entry_still_publishes(self):
        # Sanity: the guard does not block the intended happy path.
        rep_before = self.submitter.reputation_score
        self._three_upvotes(self.brand)
        self.assertTrue(evaluate_submission(self.brand))
        self.brand.refresh_from_db()
        self.submitter.refresh_from_db()
        self.assertEqual(self.brand.status, PublicationStatus.PUBLISHED)
        self.assertEqual(
            self.submitter.reputation_score, rep_before + REP_ACCEPTED_SUBMISSION
        )


# ---------------------------------------------------------------------------
# Scale-length validation on the submit path (#14/#31)
# ---------------------------------------------------------------------------
class GuitarScaleValidationTests(TestCase):
    """Negative/zero scale lengths and inverted ranges must fail validation."""

    def setUp(self):
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()
        self.brand = Brand.objects.create(
            name="ScaleBrand", status=PublicationStatus.PUBLISHED
        )
        self.country = Country.objects.create(name="USA")
        self.user = _make_user("scale_submitter", email_confirmed=True)
        self.client.force_login(self.user)
        self.url = reverse("catalog:submit_entry", args=["guitar"])

    def _payload(self, **overrides):
        data = {
            "brand": self.brand.pk,
            "name": "Scale Test",
            "num_strings": "6",
            "scale_length_min_inches": "25.5",
            "scale_length_max_inches": "25.5",
        }
        data.update(overrides)
        return data

    def test_negative_scale_min_fails_validation(self):
        response = self.client.post(
            self.url,
            self._payload(
                name="NegativeScale",
                scale_length_min_inches="-25.5",
                scale_length_max_inches="25.5",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/submit/form.html")
        self.assertFalse(GuitarModel.objects.filter(name="NegativeScale").exists())
        self.assertIn("scale_length_min_inches", response.context["form"].errors)

    def test_zero_num_strings_fails_validation(self):
        # A guitar must have at least one string (same data-quality invariant
        # as the positive-scale rule).
        response = self.client.post(
            self.url, self._payload(name="ZeroStrings", num_strings="0")
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(GuitarModel.objects.filter(name="ZeroStrings").exists())
        self.assertIn("num_strings", response.context["form"].errors)

    def test_zero_num_frets_fails_validation(self):
        response = self.client.post(
            self.url, self._payload(name="ZeroFrets", num_frets="0")
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(GuitarModel.objects.filter(name="ZeroFrets").exists())
        self.assertIn("num_frets", response.context["form"].errors)

    def test_zero_scale_min_fails_validation(self):
        response = self.client.post(
            self.url,
            self._payload(
                name="ZeroScale",
                scale_length_min_inches="0",
                scale_length_max_inches="25.5",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(GuitarModel.objects.filter(name="ZeroScale").exists())
        self.assertIn("scale_length_min_inches", response.context["form"].errors)

    def test_inverted_range_min_greater_than_max_fails(self):
        response = self.client.post(
            self.url,
            self._payload(
                name="InvertedScale",
                scale_length_min_inches="27.0",
                scale_length_max_inches="24.0",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/submit/form.html")
        self.assertFalse(GuitarModel.objects.filter(name="InvertedScale").exists())
        # Cross-field error attaches to the max field (see GuitarModel.clean).
        self.assertIn("scale_length_max_inches", response.context["form"].errors)

    def test_form_negative_scale_is_invalid(self):
        # Direct form-level check (no HTTP), proving the validator fires.
        form = GuitarForm(
            data=self._payload(scale_length_min_inches="-1", name="X")
        )
        self.assertFalse(form.is_valid())
        self.assertIn("scale_length_min_inches", form.errors)

    def test_form_inverted_range_is_invalid(self):
        form = GuitarForm(
            data=self._payload(
                scale_length_min_inches="27.0",
                scale_length_max_inches="24.0",
                name="X",
            )
        )
        self.assertFalse(form.is_valid())
        self.assertIn("scale_length_max_inches", form.errors)

    def test_equal_min_max_is_valid(self):
        # The boundary (min == max, a standard single-scale guitar) is allowed.
        form = GuitarForm(
            data=self._payload(
                scale_length_min_inches="25.5",
                scale_length_max_inches="25.5",
                name="X",
            )
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_valid_multiscale_range_is_accepted(self):
        response = self.client.post(
            self.url,
            self._payload(
                name="ValidMultiscale",
                scale_length_min_inches="25.5",
                scale_length_max_inches="27.0",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/submit/done.html")
        guitar = GuitarModel.objects.get(name="ValidMultiscale")
        self.assertEqual(guitar.scale_length_min_inches, Decimal("25.5"))
        self.assertEqual(guitar.scale_length_max_inches, Decimal("27.0"))


# ---------------------------------------------------------------------------
# GuitarForm only offers published brands/components (#28, part B)
# ---------------------------------------------------------------------------
class GuitarFormPublishedQuerysetTests(TestCase):
    """The submit form must hide under-revision/rejected dependencies."""

    def setUp(self):
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()
        self.bridge_type = BridgeType.objects.create(
            name="Hardtail", is_tremolo=False
        )
        self.published_brand = Brand.objects.create(
            name="PublishedBrand", status=PublicationStatus.PUBLISHED
        )
        self.pending_brand = Brand.objects.create(
            name="PendingBrand", status=PublicationStatus.UNDER_REVISION
        )
        self.published_bridge = Bridge.objects.create(
            brand=self.published_brand,
            name="PublishedBridge",
            bridge_type=self.bridge_type,
            status=PublicationStatus.PUBLISHED,
        )
        self.pending_bridge = Bridge.objects.create(
            brand=self.published_brand,
            name="PendingBridge",
            bridge_type=self.bridge_type,
            status=PublicationStatus.UNDER_REVISION,
        )

    def test_bridge_field_excludes_under_revision_bridge(self):
        form = GuitarForm()
        qs = form.fields["bridge"].queryset
        self.assertIn(self.published_bridge, qs)
        self.assertNotIn(self.pending_bridge, qs)

    def test_brand_field_excludes_under_revision_brand(self):
        form = GuitarForm()
        qs = form.fields["brand"].queryset
        self.assertIn(self.published_brand, qs)
        self.assertNotIn(self.pending_brand, qs)

    def test_brand_field_still_required(self):
        # Restricting the queryset must not change the field's required flag.
        form = GuitarForm()
        self.assertTrue(form.fields["brand"].required)

    def test_bridge_field_still_optional(self):
        form = GuitarForm()
        self.assertFalse(form.fields["bridge"].required)


# ---------------------------------------------------------------------------
# Referenced catalog entities cannot be deleted out from under a guitar:
# on_delete=PROTECT, never CASCADE or a silent SET_NULL. The owner's path is to
# replace/clear the component on the guitar first, THEN delete it.
# ---------------------------------------------------------------------------
class ReferencedComponentDeletionProtectedTests(TestCase):
    """Deleting a shared spec entity that a guitar references (e.g. a 'Floyd
    Rose' bridge, its brand, or its country) must raise ProtectedError — it
    must never destroy the guitar nor silently blank the spec."""

    def setUp(self):
        self.brand = Brand.objects.create(
            name="ProtBrand", status=PublicationStatus.PUBLISHED
        )
        self.btype = BridgeType.objects.create(name="Double-locking tremolo")
        self.bridge = Bridge.objects.create(
            brand=self.brand,
            name="Floyd Rose 1000",
            bridge_type=self.btype,
            status=PublicationStatus.PUBLISHED,
        )
        self.country = Country.objects.create(name="Japan")
        self.guitar = GuitarModel.objects.create(
            brand=self.brand,
            name="Shred Machine",
            status=PublicationStatus.PUBLISHED,
            num_strings=6,
            scale_length_min_inches=Decimal("25.5"),
            scale_length_max_inches=Decimal("25.5"),
            num_frets=24,
            bridge=self.bridge,
            country_of_origin=self.country,
        )

    def test_cannot_delete_bridge_in_use(self):
        with self.assertRaises(ProtectedError):
            self.bridge.delete()
        self.assertTrue(Bridge.objects.filter(pk=self.bridge.pk).exists())

    def test_cannot_delete_country_in_use(self):
        with self.assertRaises(ProtectedError):
            self.country.delete()

    def test_cannot_delete_brand_in_use(self):
        with self.assertRaises(ProtectedError):
            self.brand.delete()

    def test_blocked_delete_does_not_cascade_or_null_the_guitar(self):
        try:
            self.bridge.delete()
        except ProtectedError:
            pass
        self.guitar.refresh_from_db()
        self.assertTrue(GuitarModel.objects.filter(pk=self.guitar.pk).exists())
        self.assertEqual(self.guitar.bridge_id, self.bridge.pk)

    def test_can_delete_once_dereferenced(self):
        self.guitar.bridge = None
        self.guitar.save(update_fields=["bridge"])
        self.bridge.delete()  # no longer referenced -> deletable
        self.assertFalse(Bridge.objects.filter(pk=self.bridge.pk).exists())
