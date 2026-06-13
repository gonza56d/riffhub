from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import EmailConfirmation, User


@admin.register(User)
class RiffhubUserAdmin(UserAdmin):
    """User admin surfacing reputation, the derived level and the role flags.

    ``is_community_moderator`` / ``is_riffhub_creator`` / ``is_founder`` are
    editable so staff can grant roles and (where appropriate) seed the sticky
    Founder badge directly.
    """

    list_display = [
        "username",
        "email",
        "level_display",
        "reputation_score",
        "accepted_submissions_count",
        "is_founder",
        "is_community_moderator",
        "is_riffhub_creator",
    ]
    list_filter = UserAdmin.list_filter + (
        "is_founder",
        "is_community_moderator",
        "is_riffhub_creator",
        "email_confirmed",
    )
    list_editable = [
        "is_founder",
        "is_community_moderator",
        "is_riffhub_creator",
    ]
    readonly_fields = [
        "reputation_score",
        "accepted_submissions_count",
        "rejected_submissions_count",
    ]
    fieldsets = UserAdmin.fieldsets + (
        (
            "Riffhub standing",
            {
                "fields": (
                    "email_confirmed",
                    "reputation_score",
                    "accepted_submissions_count",
                    "rejected_submissions_count",
                    "is_founder",
                    "is_community_moderator",
                    "is_riffhub_creator",
                )
            },
        ),
    )

    @admin.display(description="Level")
    def level_display(self, obj: User) -> str:
        return obj.level.label


@admin.register(EmailConfirmation)
class EmailConfirmationAdmin(admin.ModelAdmin):
    list_display = ["user", "confirmed_at", "created_at"]
    list_filter = ["confirmed_at"]
    search_fields = ["user__username", "user__email"]
    readonly_fields = ["token", "confirmed_at", "created_at", "updated_at"]
