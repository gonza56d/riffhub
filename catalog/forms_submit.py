"""Collab-db submission forms.

ModelForms for the catalog entities a logged-in, e-mail-confirmed user may
propose. Each form exposes ONLY the user-entered fields — the workflow columns
(``status``/``submitted_by``/timestamps), the auto-computed guitar facets and
the pickup M2M are deliberately excluded (the view sets ``submitted_by`` and
the model defaults the status to ``UNDER_REVISION``; facets recompute via
signals once pickups are attached).
"""

from django import forms

from catalog.models import Brand, Bridge, GuitarModel, Nut, Pickup, Tuner


class BrandForm(forms.ModelForm):
    class Meta:
        model = Brand
        fields = ["name", "country", "website", "description"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }


class BridgeForm(forms.ModelForm):
    class Meta:
        model = Bridge
        fields = [
            "brand",
            "name",
            "description",
            "image",
            "bridge_type",
            "has_piezo",
            "is_locking",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }


class PickupForm(forms.ModelForm):
    class Meta:
        model = Pickup
        fields = [
            "brand",
            "name",
            "description",
            "image",
            "pickup_type",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }


class TunerForm(forms.ModelForm):
    class Meta:
        model = Tuner
        fields = [
            "brand",
            "name",
            "description",
            "image",
            "is_locking",
            "ratio",
            "tuner_type",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }


class NutForm(forms.ModelForm):
    class Meta:
        model = Nut
        fields = [
            "brand",
            "name",
            "description",
            "image",
            "material",
            "is_locking",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }


class GuitarForm(forms.ModelForm):
    """All hand-entered guitar specs.

    Excludes the derived facets (``pickup_combination``, ``electronics_type``,
    ``has_*``, ``neck_thickness_class``, ``is_multiscale``), the ``pickups``
    M2M (handled as separate position slots in the view) and the workflow /
    timestamp columns.
    """

    class Meta:
        model = GuitarModel
        fields = [
            # Identity
            "brand",
            "name",
            "year_introduced",
            "year_discontinued",
            # Strings & scale (required)
            "num_strings",
            "scale_length_min_inches",
            "scale_length_max_inches",
            # Frets & fretboard
            "num_frets",
            "fret_material",
            "is_fretless",
            "fretboard_material",
            "fretboard_radius",
            # Neck
            "neck_construction",
            "neck_material",
            "neck_profile",
            "neck_depth_1st_fret_mm",
            "neck_depth_12th_fret_mm",
            "nut_width_mm",
            # Body & hardware
            "body_material",
            "body_shape",
            "headstock_type",
            "selector_switch",
            "country_of_origin",
            # Components
            "bridge",
            "nut",
            "tuners",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only already-published brands/components may be chosen at creation, so
        # the submit form can't smuggle in an un-reviewed dependency (the
        # review-gate bypass). Reassigning the queryset leaves each field's
        # required/optional flag (brand required; the components optional) intact.
        self.fields["brand"].queryset = Brand.objects.published()
        self.fields["bridge"].queryset = Bridge.objects.published()
        self.fields["nut"].queryset = Nut.objects.published()
        self.fields["tuners"].queryset = Tuner.objects.published()
