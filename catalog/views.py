"""Catalog browse + live filtering.

A single view renders the full browse page on a normal request and just the
results fragment on an HTMX request, so filtering updates in place (and the URL
stays shareable via ``hx-push-url``). All the filterable facets — including the
ones *derived* from components — are plain indexed columns, so this is simple,
fast ORM filtering.
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import F
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from catalog import services
from catalog.constants import ElectronicsType, PublicationStatus
from catalog.models import (
    BodyShape,
    Bridge,
    CatalogComment,
    Country,
    GuitarModel,
    NeckConstruction,
    Nut,
    Pickup,
    Tuner,
)

# How many guitars to show per page on the browse list.
PAGE_SIZE = 24

# (query-param flag, human label) for the boolean facets shown as checkboxes.
BOOLEAN_FACETS = [
    ("has_tremolo", "Tremolo"),
    ("has_locking_tuners", "Locking tuners"),
    ("has_hum_cancellation", "Hum-cancelling"),
    ("has_piezo", "Piezo"),
    ("is_multiscale", "Multiscale"),
    ("is_fretless", "Fretless"),
]


def _truthy(value: str) -> bool:
    return value in ("1", "on", "true", "yes")


def _as_int(value):
    """Parse a query-param value as an int, or ``None`` if it isn't one.

    Keeps junk facet values (``?neck=abc``) from blowing up the FK filters —
    an unparsable value is treated as "no filter".
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_decimal(value):
    """Parse a query-param value as a *finite* Decimal, or ``None`` if it isn't
    one. Treats ``?scale=foo`` as "no filter" rather than crashing — and also
    rejects the special ``NaN``/``Infinity`` decimals, which parse cleanly but
    blow up the DB comparison."""
    try:
        result = Decimal(value)
    except (TypeError, ValueError, InvalidOperation):
        return None
    return result if result.is_finite() else None


def filter_guitars(params):
    """Build the published-guitar queryset from the request's GET params."""
    qs = GuitarModel.objects.published().select_related(
        "brand", "body_shape", "neck_construction", "country_of_origin",
        "fretboard_radius",
    )

    strings = [s for s in params.getlist("strings") if s.isdigit()]
    if strings:
        qs = qs.filter(num_strings__in=strings)

    frets = [f for f in params.getlist("frets") if f.isdigit()]
    if frets:
        qs = qs.filter(num_frets__in=frets)

    combos = params.getlist("combo")
    if combos:
        qs = qs.filter(pickup_combination__in=combos)

    if params.get("electronics"):
        qs = qs.filter(electronics_type=params["electronics"])
    neck = _as_int(params.get("neck"))
    if neck is not None:
        qs = qs.filter(neck_construction_id=neck)
    shape = _as_int(params.get("shape"))
    if shape is not None:
        qs = qs.filter(body_shape_id=shape)
    country = _as_int(params.get("country"))
    if country is not None:
        qs = qs.filter(country_of_origin_id=country)
    scale = _as_decimal(params.get("scale"))
    if scale is not None:
        qs = qs.filter(
            scale_length_min_inches=scale,
            scale_length_max_inches=scale,
        )

    for field, _label in BOOLEAN_FACETS:
        if _truthy(params.get(field, "")):
            qs = qs.filter(**{field: True})

    return qs


def _distinct(field):
    return (
        GuitarModel.objects.published()
        .exclude(**{f"{field}__isnull": True})
        .order_by(field)
        .values_list(field, flat=True)
        .distinct()
    )


def _single_scale_values():
    return (
        GuitarModel.objects.published()
        .filter(scale_length_min_inches=F("scale_length_max_inches"))
        .order_by("scale_length_min_inches")
        .values_list("scale_length_min_inches", flat=True)
        .distinct()
    )


def _active_filter_count(params) -> int:
    keys = ["strings", "frets", "combo", "electronics", "neck", "shape",
            "scale", "country"] + [f for f, _ in BOOLEAN_FACETS]
    return sum(1 for k in keys if params.get(k))


def _page_number(raw):
    """Normalise the ``?page`` value before handing it to ``Paginator``.

    A sub-1 page (``0``, ``-3``) should mean the first page — the same fallback
    a non-numeric value gets. Left as-is, ``get_page`` raises ``EmptyPage`` for
    ``< 1`` and clamps to the *last* page, a surprising place to land. Non-numeric
    values are passed through so ``get_page`` runs its own page-1 fallback.
    """
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return raw


def guitar_browse(request):
    params = request.GET
    guitars = filter_guitars(params)
    paginator = Paginator(guitars, PAGE_SIZE)
    # get_page is forgiving: a non-integer page falls back to 1, an out-of-range
    # (too-high) page clamps to the last page — so junk/stale ?page values never
    # 404. _page_number additionally maps a sub-1 page to the first page.
    page_obj = paginator.get_page(_page_number(params.get("page")))

    # The active filters as a query string, minus the page number, so the pager
    # links carry the current selection. Changing a filter submits the form
    # *without* a page param, which naturally resets the user to page 1.
    filter_params = params.copy()
    filter_params.pop("page", None)
    base_qs = filter_params.urlencode()

    context = {
        "page_obj": page_obj,
        "base_qs": base_qs,
        # A compact page range with ellipses (… for skipped runs) for the pager.
        "page_range": list(
            paginator.get_elided_page_range(
                page_obj.number, on_each_side=1, on_ends=1
            )
        ),
        "ellipsis": paginator.ELLIPSIS,
        "total": paginator.count,
        # facet option lists
        "strings_options": list(_distinct("num_strings")),
        "frets_options": list(_distinct("num_frets")),
        "combo_options": [c for c in _distinct("pickup_combination") if c],
        "scale_options": list(_single_scale_values()),
        "electronics_options": [
            (v, lbl) for v, lbl in ElectronicsType.choices if v != ElectronicsType.UNKNOWN
        ],
        "neck_options": NeckConstruction.objects.all(),
        "shape_options": BodyShape.objects.all(),
        "country_options": Country.objects.all(),
        "boolean_facets": BOOLEAN_FACETS,
        # current selections (strings, for re-checking the form)
        "sel_strings": params.getlist("strings"),
        "sel_frets": params.getlist("frets"),
        "sel_combos": params.getlist("combo"),
        "sel_scale": params.get("scale", ""),
        "sel_electronics": params.get("electronics", ""),
        "sel_neck": params.get("neck", ""),
        "sel_shape": params.get("shape", ""),
        "sel_country": params.get("country", ""),
        "sel_flags": [f for f, _ in BOOLEAN_FACETS if _truthy(params.get(f, ""))],
        "active_count": _active_filter_count(params),
    }
    template = (
        "catalog/_guitar_results.html"
        if request.headers.get("HX-Request")
        else "catalog/guitar_browse.html"
    )
    return render(request, template, context)


def guitar_detail(request, pk):
    """Full spec sheet for one published guitar, plus its components.

    Only published guitars are reachable here — under-revision submissions stay
    hidden until accepted (they'll surface in the collab section instead).
    """
    guitar = get_object_or_404(
        GuitarModel.objects.published().select_related(
            "brand", "body_shape", "body_material", "neck_construction",
            "neck_material", "neck_profile", "fret_material", "fretboard_material",
            "fretboard_radius", "headstock_type", "selector_switch",
            "country_of_origin", "bridge__brand", "bridge__bridge_type",
            "nut__brand", "nut__material", "tuners__brand",
        ),
        pk=pk,
    )
    # Only surface attached components that are themselves PUBLISHED — an
    # unreviewed or rejected component (or one whose brand is still pending)
    # must not leak onto the public spec sheet.
    for attr in ("bridge", "nut", "tuners"):
        if not _is_published_component(getattr(guitar, attr)):
            setattr(guitar, attr, None)
    pickups = [
        gp
        for gp in guitar.guitar_pickups.select_related(
            "pickup__brand", "pickup__pickup_type"
        )
        if _is_published_component(gp.pickup)
    ]
    return render(
        request,
        "catalog/guitar_detail.html",
        {
            "guitar": guitar,
            "pickups": pickups,
            **_comment_context(request, guitar, "guitar"),
        },
    )


def _is_published_component(component) -> bool:
    """Whether an attached gear component should render on the public page.

    A component surfaces only when both it and its brand are PUBLISHED; a blank
    slot (``None``) is treated as nothing to render.
    """
    if component is None:
        return False
    if component.status != PublicationStatus.PUBLISHED:
        return False
    brand = component.brand
    return brand is not None and brand.status == PublicationStatus.PUBLISHED


# ---------------------------------------------------------------------------
# Comments (guitar + gear detail pages) + gear detail pages
# ---------------------------------------------------------------------------
PAGE_SIZE_COMMENTS = 10

# URL "kind" -> gear model. "guitar" is handled separately (GuitarModel).
GEAR_KINDS = {
    "bridge": Bridge,
    "pickup": Pickup,
    "tuner": Tuner,
    "nut": Nut,
}


def _published_gear(model, pk):
    """Fetch a published gear item whose brand is also published, else 404."""
    gear = get_object_or_404(model.objects.select_related("brand"), pk=pk)
    if not _is_published_component(gear):
        raise Http404("This gear isn't available.")
    return gear


def _comment_context(request, target, target_kind) -> dict:
    """Paginated comment thread context for a catalog detail page.

    ``target_kind`` is the URL token ("guitar" / "bridge" / …) the comment form
    posts back to. The pager links carry only ``?page`` (detail pages have no
    other query params).
    """
    paginator = Paginator(services.catalog_comment_thread(target), PAGE_SIZE_COMMENTS)
    page_obj = paginator.get_page(_page_number(request.GET.get("page")))
    return {
        "comments_page": page_obj,
        "comments_total": paginator.count,
        "comment_target_kind": target_kind,
        "comment_target_pk": target.pk,
        "can_comment": request.user.is_authenticated,
    }


def _gear_specs(kind, gear):
    """(label, value) rows for a gear item's spec sheet, by kind."""
    rows = [("Brand", gear.brand.name)]
    if kind == "bridge":
        rows += [
            ("Type", gear.bridge_type.name),
            ("Piezo", "Yes" if gear.has_piezo else "No"),
            ("Locking", "Yes" if gear.is_locking else "No"),
        ]
    elif kind == "pickup":
        rows += [
            ("Type", gear.pickup_type.name),
            ("Electronics", "Active" if gear.is_active else "Passive"),
        ]
    elif kind == "tuner":
        rows.append(("Locking", "Yes" if gear.is_locking else "No"))
        if gear.ratio:
            rows.append(("Ratio", gear.ratio))
        if gear.tuner_type:
            rows.append(("Type", gear.get_tuner_type_display()))
    elif kind == "nut":
        rows += [
            ("Material", gear.material.name),
            ("Locking", "Yes" if gear.is_locking else "No"),
        ]
    return rows


def _guitars_using(kind, gear):
    """Published guitars that reference this gear item (for the "Used on" list)."""
    qs = GuitarModel.objects.published().select_related("brand")
    if kind == "bridge":
        qs = qs.filter(bridge=gear)
    elif kind == "tuner":
        qs = qs.filter(tuners=gear)
    elif kind == "nut":
        qs = qs.filter(nut=gear)
    elif kind == "pickup":
        qs = qs.filter(guitar_pickups__pickup=gear).distinct()
    return qs.order_by("brand__name", "name")


def gear_detail(request, kind, pk):
    """Spec sheet for one published gear item, the guitars using it, and its
    comment thread. Mirrors ``guitar_detail``'s publication gating."""
    model = GEAR_KINDS.get(kind)
    if model is None:
        raise Http404("Unknown gear type.")
    gear = _published_gear(model, pk)
    context = {
        "kind": kind,
        "gear": gear,
        "specs": _gear_specs(kind, gear),
        "description": gear.description,
        "used_on": _guitars_using(kind, gear),
        **_comment_context(request, gear, kind),
    }
    return render(request, "catalog/gear_detail.html", context)


def _resolve_comment_target(kind, pk):
    """Resolve a comment-target URL token to its published catalog object."""
    if kind == "guitar":
        return get_object_or_404(GuitarModel.objects.published(), pk=pk)
    model = GEAR_KINDS.get(kind)
    if model is None:
        raise Http404("Unknown comment target.")
    return _published_gear(model, pk)


@require_POST
def add_catalog_comment(request, kind, pk):
    """Post a comment (or a one-level reply via a ``parent`` field) on a guitar
    or gear page, then redirect back to it."""
    if not request.user.is_authenticated:
        return redirect("login")
    target = _resolve_comment_target(kind, pk)

    parent = None
    parent_id = request.POST.get("parent")
    if parent_id:
        parent = get_object_or_404(CatalogComment, pk=parent_id)

    try:
        services.add_catalog_comment(
            target=target,
            author=request.user,
            body=request.POST.get("body", ""),
            parent=parent,
        )
    except PermissionDenied as exc:
        messages.error(request, str(exc) or "You can't comment right now.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))

    if kind == "guitar":
        return redirect("catalog:detail", pk=target.pk)
    return redirect("catalog:gear_detail", kind=kind, pk=target.pk)
