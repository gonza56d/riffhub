"""Collab-db review / voting UI.

Database Collaborators (and higher) review UNDER_REVISION submissions, cast
+1/−1 review votes and file corrections; an entry auto-publishes the moment its
votes clear the configured bar.

Read pages (the queue and a submission's detail) are open to any logged-in user
so the process is transparent; the *actions* (vote, correct) require
Collaborator+. The engagement actions are HTMX endpoints that return the
re-rendered widget fragment so the page updates in place — modelled on the
forum's vote/comment endpoints. All business rules live in ``catalog.services``;
these views only resolve targets, enforce auth and pick what HTML to send back.
"""

from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from accounts.models import Level
from catalog.constants import CorrectionStatus, VoteValue
from catalog.forms_review import CorrectionForm
from catalog.models import (
    Brand,
    Bridge,
    Correction,
    GuitarModel,
    Nut,
    Pickup,
    ReviewVote,
    Tuner,
)
from catalog.services import cast_review_vote, evaluate_submission

# kind (URL slug) -> (model, human label). The order here is the order the
# queue groups types in when sorting is otherwise tied.
KINDS = {
    "guitar": (GuitarModel, "Guitar"),
    "brand": (Brand, "Brand"),
    "bridge": (Bridge, "Bridge"),
    "pickup": (Pickup, "Pickup"),
    "tuner": (Tuner, "Tuner"),
    "nut": (Nut, "Nut"),
}


# --- helpers ---------------------------------------------------------------
def _resolve_target(kind, pk):
    """Resolve ``(kind, pk)`` to a model instance; 404 on unknown kind / pk."""
    entry = KINDS.get(kind)
    if entry is None:
        raise Http404("Unknown submission type.")
    model = entry[0]
    return get_object_or_404(model, pk=pk)


def _kind_label(kind):
    entry = KINDS.get(kind)
    return entry[1] if entry else kind.title()


def _my_vote(user, target):
    """The signed-in user's current vote value on ``target`` (or ``None``)."""
    if not user.is_authenticated:
        return None
    ct = ContentType.objects.get_for_model(target)
    row = ReviewVote.objects.filter(
        voter=user, content_type=ct, object_id=target.pk
    ).first()
    return row.value if row else None


def _can_review(user):
    return user.is_authenticated and user.is_at_least(Level.COLLABORATOR)


def _vote_ctx(target, kind, user, *, just_published=False):
    """Context for the ``_review_vote.html`` fragment."""
    mine = _my_vote(user, target)
    return {
        "kind": kind,
        "obj": target,
        "net": ReviewVote.net_votes(target),
        "voters": ReviewVote.voter_count(target),
        "up_active": mine == VoteValue.UP,
        "down_active": mine == VoteValue.DOWN,
        # A submitter can't vote on their own entry, mirroring the service.
        "can_vote": _can_review(user)
        and getattr(target, "submitted_by_id", None) != user.pk,
        "is_published": target.status == "published",
        "just_published": just_published,
    }


def _corrections_ctx(target, kind, user):
    """Context for the ``_corrections.html`` fragment."""
    ct = ContentType.objects.get_for_model(target)
    corrections = (
        Correction.objects.filter(content_type=ct, object_id=target.pk)
        .select_related("author", "resolved_by")
    )
    return {
        "kind": kind,
        "obj": target,
        "corrections": corrections,
        "can_correct": _can_review(user),
        "form": CorrectionForm(),
        "CorrectionStatus": CorrectionStatus,
    }


# --- read pages ------------------------------------------------------------
@login_required
def review_queue(request):
    """List every UNDER_REVISION entry across the catalog types.

    Open to any logged-in user (transparency); non-collaborators get a notice
    but can still browse. Each model is queried with ``.under_revision()`` and
    the rows are merged and sorted newest-first.
    """
    rows = []
    for kind, (model, label) in KINDS.items():
        qs = model.objects.under_revision().select_related("submitted_by")
        if kind == "guitar":
            qs = qs.select_related("brand")
        for obj in qs:
            rows.append(
                {
                    "kind": kind,
                    "label": label,
                    "obj": obj,
                    "submitter": obj.submitted_by,
                    "net": ReviewVote.net_votes(obj),
                }
            )
    rows.sort(key=lambda r: r["obj"].created_at, reverse=True)

    return render(
        request,
        "catalog/review/queue.html",
        {
            "rows": rows,
            "can_review": _can_review(request.user),
        },
    )


@login_required
def review_detail(request, kind, pk):
    """A single submission's proposed fields, vote widget and corrections."""
    target = _resolve_target(kind, pk)

    pickups = None
    if kind == "guitar":
        pickups = list(
            target.guitar_pickups.select_related(
                "pickup__brand", "pickup__pickup_type"
            )
        )

    return render(
        request,
        "catalog/review/detail.html",
        {
            "kind": kind,
            "kind_label": _kind_label(kind),
            "obj": target,
            "pickups": pickups,
            "submitter": target.submitted_by,
            "can_review": _can_review(request.user),
            "vote": _vote_ctx(target, kind, request.user),
            "corrections": _corrections_ctx(target, kind, request.user),
        },
    )


# --- engagement actions (HTMX) ---------------------------------------------
@require_POST
def review_vote(request, kind, pk, value):
    """Cast/toggle a +1/−1 review vote, then auto-publish if the bar is met."""
    if not _can_review(request.user):
        return HttpResponse(
            "Only Database Collaborators and above can vote on submissions.",
            status=403,
        )
    target = _resolve_target(kind, pk)
    val = VoteValue.UP if value == "up" else VoteValue.DOWN
    try:
        cast_review_vote(request.user, target, val)
    except PermissionError as exc:
        return HttpResponse(str(exc), status=403)

    just_published = evaluate_submission(target)
    # Re-fetch so the freshly published status / tally is reflected.
    target = _resolve_target(kind, pk)
    return render(
        request,
        "catalog/review/_review_vote.html",
        {"v": _vote_ctx(target, kind, request.user, just_published=just_published)},
    )


@require_POST
def add_correction(request, kind, pk):
    """File a correction on a submission and return the re-rendered list."""
    if not _can_review(request.user):
        return HttpResponse(
            "Only Database Collaborators and above can file corrections.",
            status=403,
        )
    target = _resolve_target(kind, pk)
    form = CorrectionForm(request.POST)
    if form.is_valid():
        correction = form.save(commit=False)
        correction.author = request.user
        correction.content_type = ContentType.objects.get_for_model(target)
        correction.object_id = target.pk
        correction.status = CorrectionStatus.OPEN
        correction.save()
    return render(
        request,
        "catalog/review/_corrections.html",
        {"c": _corrections_ctx(target, kind, request.user)},
    )
