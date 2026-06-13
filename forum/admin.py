from django.contrib import admin

from . import models


class SubtopicInline(admin.TabularInline):
    model = models.Subtopic
    extra = 0
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["activity_count"]


@admin.register(models.Topic)
class TopicAdmin(admin.ModelAdmin):
    inlines = [SubtopicInline]
    list_display = [
        "name",
        "is_market",
        "requires_disclaimer",
        "is_predefined",
        "activity_count",
    ]
    list_filter = ["is_market", "requires_disclaimer", "is_predefined"]
    search_fields = ["name", "description"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["activity_count"]


@admin.register(models.Subtopic)
class SubtopicAdmin(admin.ModelAdmin):
    list_display = ["name", "topic", "activity_count"]
    list_filter = ["topic"]
    search_fields = ["name", "topic__name"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["activity_count"]


@admin.register(models.Post)
class PostAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "subtopic",
        "author",
        "price",
        "currency",
        "activity_count",
        "created_at",
    ]
    list_filter = ["subtopic__topic", "subtopic", "created_at"]
    search_fields = ["title", "body", "author__username"]
    raw_id_fields = ["author", "subtopic"]
    readonly_fields = ["activity_count", "created_at", "updated_at"]


@admin.register(models.Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ["__str__", "post", "author", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["body", "author__username", "post__title"]
    raw_id_fields = ["author", "post"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(models.Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ["voter", "value", "content_type", "object_id", "created_at"]
    list_filter = ["value", "content_type"]
    search_fields = ["voter__username"]
    raw_id_fields = ["voter"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(models.Reaction)
class ReactionAdmin(admin.ModelAdmin):
    list_display = ["user", "emoji", "content_type", "object_id", "created_at"]
    list_filter = ["emoji", "content_type"]
    search_fields = ["user__username", "emoji"]
    raw_id_fields = ["user"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(models.Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ["__str__", "uploaded_by", "content_type", "object_id", "created_at"]
    list_filter = ["content_type"]
    search_fields = ["uploaded_by__username", "caption"]
    raw_id_fields = ["uploaded_by"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(models.MarketDisclaimerAcceptance)
class MarketDisclaimerAcceptanceAdmin(admin.ModelAdmin):
    list_display = ["user", "accepted_at"]
    search_fields = ["user__username"]
    raw_id_fields = ["user"]
    readonly_fields = ["accepted_at"]


class ProposalVoteInline(admin.TabularInline):
    model = models.ProposalVote
    extra = 0
    raw_id_fields = ["voter"]
    readonly_fields = ["created_at", "updated_at"]
    # A ProposalVote points at either a topic or subtopic proposal; the inline
    # is reused on both admins and only shows its own relation's rows.


@admin.register(models.TopicProposal)
class TopicProposalAdmin(admin.ModelAdmin):
    inlines = [ProposalVoteInline]
    list_display = ["proposed_name", "proposer", "status", "opened_at", "closes_at"]
    list_filter = ["status"]
    search_fields = ["proposed_name", "proposer__username"]
    raw_id_fields = ["proposer"]
    readonly_fields = ["opened_at", "created_at", "updated_at"]


@admin.register(models.SubtopicProposal)
class SubtopicProposalAdmin(admin.ModelAdmin):
    inlines = [ProposalVoteInline]
    list_display = [
        "proposed_name",
        "parent_topic",
        "proposer",
        "status",
        "opened_at",
        "closes_at",
    ]
    list_filter = ["status", "parent_topic"]
    search_fields = ["proposed_name", "proposer__username", "parent_topic__name"]
    raw_id_fields = ["proposer", "parent_topic"]
    readonly_fields = ["opened_at", "created_at", "updated_at"]


@admin.register(models.ProposalVote)
class ProposalVoteAdmin(admin.ModelAdmin):
    list_display = ["voter", "value", "topic_proposal", "subtopic_proposal", "created_at"]
    list_filter = ["value"]
    search_fields = ["voter__username"]
    raw_id_fields = ["voter", "topic_proposal", "subtopic_proposal"]
    readonly_fields = ["created_at", "updated_at"]
