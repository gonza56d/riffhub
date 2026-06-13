from django.contrib import admin

from .models import Ban, ContentAction, Silence, Warning


@admin.register(Warning)
class WarningAdmin(admin.ModelAdmin):
    list_display = ["target", "issued_by", "created_at"]
    search_fields = ["target__username", "reason"]
    raw_id_fields = ["target", "issued_by"]


@admin.register(Silence)
class SilenceAdmin(admin.ModelAdmin):
    list_display = ["target", "sequence", "is_permanent", "ends_at", "is_active", "created_at"]
    list_filter = ["is_permanent", "is_public_flag"]
    search_fields = ["target__username", "reason"]
    raw_id_fields = ["target", "issued_by"]


@admin.register(Ban)
class BanAdmin(admin.ModelAdmin):
    list_display = ["target", "issued_by", "is_active", "lifted_at", "created_at"]
    search_fields = ["target__username", "reason"]
    raw_id_fields = ["target", "issued_by"]


@admin.register(ContentAction)
class ContentActionAdmin(admin.ModelAdmin):
    list_display = ["action", "moderator", "content_type", "object_id", "created_at"]
    list_filter = ["action"]
    raw_id_fields = ["moderator"]
