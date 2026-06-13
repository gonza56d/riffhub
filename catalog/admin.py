from django.contrib import admin

from . import models

# --- Plain controlled vocabularies -----------------------------------------
for _vocab in (
    models.FretMaterial,
    models.FretboardMaterial,
    models.NeckConstruction,
    models.NeckMaterial,
    models.NeckProfile,
    models.BodyMaterial,
    models.BodyShape,
    models.HeadstockType,
    models.SelectorSwitch,
    models.NutMaterial,
    models.Country,
):
    admin.site.register(_vocab)


@admin.register(models.FretboardRadius)
class FretboardRadiusAdmin(admin.ModelAdmin):
    list_display = ["name", "radius_min_inches", "radius_max_inches", "is_compound", "is_flat"]


@admin.register(models.PickupType)
class PickupTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "symbol", "is_humbucking"]


@admin.register(models.BridgeType)
class BridgeTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "is_tremolo", "is_locking"]


@admin.register(models.Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ["name", "country", "status"]
    list_filter = ["status", "country"]
    search_fields = ["name"]


class GearAdmin(admin.ModelAdmin):
    list_display = ["__str__", "brand", "status"]
    list_filter = ["status", "brand"]
    search_fields = ["name", "brand__name"]


admin.site.register(models.Bridge, GearAdmin)
admin.site.register(models.Pickup, GearAdmin)
admin.site.register(models.Tuner, GearAdmin)
admin.site.register(models.Nut, GearAdmin)


class GuitarPickupInline(admin.TabularInline):
    model = models.GuitarPickup
    extra = 0
    autocomplete_fields = ["pickup"]


@admin.register(models.GuitarModel)
class GuitarModelAdmin(admin.ModelAdmin):
    inlines = [GuitarPickupInline]
    list_display = [
        "__str__", "num_strings", "scale_display", "pickup_combination",
        "electronics_type", "has_tremolo", "is_multiscale", "status",
    ]
    list_filter = [
        "status", "num_strings", "is_multiscale", "has_tremolo", "has_piezo",
        "has_locking_tuners", "has_hum_cancellation", "electronics_type",
        "neck_construction", "country_of_origin",
    ]
    search_fields = ["name", "brand__name"]
    readonly_fields = [
        "pickup_combination", "electronics_type", "has_hum_cancellation",
        "has_tremolo", "has_piezo", "has_locking_tuners", "neck_thickness_class",
        "is_multiscale",
    ]

    @admin.display(description="Scale")
    def scale_display(self, obj):
        lo, hi = obj.scale_length_min_inches, obj.scale_length_max_inches
        return f'{lo}"' if lo == hi else f'{lo}"–{hi}"'


# --- Collab-db review workflow ---------------------------------------------
@admin.register(models.ReviewVote)
class ReviewVoteAdmin(admin.ModelAdmin):
    list_display = ["voter", "value", "content_type", "object_id", "target", "created_at"]
    list_filter = ["value", "content_type"]
    search_fields = ["voter__username"]
    raw_id_fields = ["voter"]


@admin.register(models.Correction)
class CorrectionAdmin(admin.ModelAdmin):
    list_display = ["__str__", "author", "status", "resolved_by", "created_at"]
    list_filter = ["status", "content_type"]
    search_fields = ["author__username", "body"]
    raw_id_fields = ["author", "resolved_by"]
