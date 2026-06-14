"""Collab-db SUBMISSION views.

Logged-in, e-mail-confirmed users propose new catalog entries (guitars, gear,
brands). Submissions are created with the model default ``status =
UNDER_REVISION`` and ``submitted_by`` set to the current user, then land in the
community REVIEW queue (built separately).

The gate is ``catalog.services.can_submit_to_collab`` — it is False when the
e-mail is unconfirmed or the user is in a reject cooldown.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render

from catalog.constants import PickupPosition
from catalog.forms_submit import (
    BrandForm,
    BridgeForm,
    GuitarForm,
    NutForm,
    PickupForm,
    TunerForm,
)
from catalog.models import (
    Brand,
    Bridge,
    GuitarModel,
    GuitarPickup,
    Nut,
    Pickup,
    Tuner,
)
from catalog.services import can_submit_to_collab

# kind -> (model, form_class, human label)
SUBMISSION_KINDS = {
    "guitar": (GuitarModel, GuitarForm, "Guitar model"),
    "brand": (Brand, BrandForm, "Brand"),
    "bridge": (Bridge, BridgeForm, "Bridge"),
    "pickup": (Pickup, PickupForm, "Pickup"),
    "tuner": (Tuner, TunerForm, "Tuner"),
    "nut": (Nut, NutForm, "Nut"),
}

# The three optional guitar pickup slots: (form field name, position value).
PICKUP_SLOTS = [
    ("pickup_bridge", PickupPosition.BRIDGE),
    ("pickup_middle", PickupPosition.MIDDLE),
    ("pickup_neck", PickupPosition.NECK),
]


@login_required
def submit_index(request):
    """Landing page for the collab-db submission section.

    If the user has not cleared the submission gate (unconfirmed e-mail or
    reject cooldown), show a notice instead of the kind cards.
    """
    can_submit = can_submit_to_collab(request.user)
    kinds = [
        {"slug": slug, "label": label}
        for slug, (_model, _form, label) in SUBMISSION_KINDS.items()
    ]
    return render(
        request,
        "catalog/submit/index.html",
        {"can_submit": can_submit, "kinds": kinds},
    )


@login_required
def submit_entry(request, kind):
    """Generic create view for one submittable ``kind``.

    GET renders an unbound form (plus pickup slots for guitars). POST validates,
    sets ``submitted_by`` and saves (status stays ``UNDER_REVISION``); for a
    guitar it also creates ``GuitarPickup`` rows for any filled pickup slot,
    which triggers the derived-facet recompute via signals.
    """
    entry = SUBMISSION_KINDS.get(kind)
    if entry is None:
        raise Http404("Unknown submission kind.")
    model, form_class, label = entry

    if not can_submit_to_collab(request.user):
        messages.error(
            request,
            "Confirm your e-mail before contributing to the database.",
        )
        return redirect("catalog:submit_index")

    is_guitar = kind == "guitar"

    if request.method == "POST":
        form = form_class(request.POST, request.FILES)
        if form.is_valid():
            # Create the entry and attach any components atomically, so a bad
            # pickup slot can't leave an orphaned under-revision guitar behind.
            with transaction.atomic():
                obj = form.save(commit=False)
                obj.submitted_by = request.user
                obj.save()
                if is_guitar:
                    _attach_pickups(obj, request.POST)
            messages.success(
                request,
                f"{label} submitted — it's now pending community review.",
            )
            return render(
                request,
                "catalog/submit/done.html",
                {"label": label, "obj": obj},
            )
    else:
        form = form_class()

    return render(
        request,
        "catalog/submit/form.html",
        {
            "form": form,
            "kind": kind,
            "label": label,
            "is_guitar": is_guitar,
            "pickups": Pickup.objects.published() if is_guitar else None,
            "pickup_slots": PICKUP_SLOTS if is_guitar else None,
        },
    )


def _attach_pickups(guitar, post) -> None:
    """Create a ``GuitarPickup`` row for each filled optional pickup slot.

    Silently ignores blank slots, a non-integer slot id, and any slot pointing
    at a missing or unpublished pickup id; the form itself is the source of
    truth for the guitar's own fields, while pickups are optional extras chosen
    from existing published catalog entries.
    """
    for field_name, position in PICKUP_SLOTS:
        raw_id = post.get(field_name)
        if not raw_id:
            continue
        try:
            pickup_id = int(raw_id)
        except (TypeError, ValueError):
            # A non-integer slot id points at no pickup — skip it.
            continue
        pickup = Pickup.objects.published().filter(pk=pickup_id).first()
        if pickup is None:
            continue
        GuitarPickup.objects.create(
            guitar=guitar, pickup=pickup, position=position
        )
