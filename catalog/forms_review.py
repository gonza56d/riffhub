"""Forms for the collab-db review / voting UI.

Only the correction *body* is user-supplied; the view sets author, target and
status when it creates the ``Correction`` (mirrors how the forum's HTMX actions
keep authorship server-side).
"""

from django import forms

from catalog.models import Correction


class CorrectionForm(forms.ModelForm):
    """Propose a correction to a catalog entry — body only."""

    class Meta:
        model = Correction
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(
                attrs={
                    "rows": 3,
                    "required": True,
                    "placeholder": "Describe the fix (wrong spec, missing part…)",
                }
            ),
        }
        labels = {"body": "Proposed correction"}
