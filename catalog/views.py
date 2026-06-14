"""Catalog browse + live filtering.

A single view renders the full browse page on a normal request and just the
results fragment on an HTMX request, so filtering updates in place (and the URL
stays shareable via ``hx-push-url``). All the filterable facets — including the
ones *derived* from components — are plain indexed columns, so this is simple,
fast ORM filtering.
"""

from decimal import Decimal, InvalidOperation

from django.db.models import F
from django.shortcuts import get_object_or_404, render

from catalog.constants import ElectronicsType, PublicationStatus
from catalog.models import BodyShape, Country, GuitarModel, NeckConstruction

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


def guitar_browse(request):
    params = request.GET
    guitars = filter_guitars(params)
    context = {
        "guitars": guitars,
        "total": guitars.count(),
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
        {"guitar": guitar, "pickups": pickups},
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
