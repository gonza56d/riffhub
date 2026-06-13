from django.contrib import admin

from messaging.models import Conversation, DirectMessage, DirectMessageReport


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["__str__", "user_low", "user_high", "last_message_at"]
    raw_id_fields = ["user_low", "user_high"]
    readonly_fields = ["created_at", "updated_at"]
    search_fields = ["user_low__username", "user_high__username"]


@admin.register(DirectMessage)
class DirectMessageAdmin(admin.ModelAdmin):
    list_display = ["__str__", "conversation", "sender", "is_read", "created_at"]
    list_filter = ["is_read", "created_at"]
    raw_id_fields = ["conversation", "sender"]
    readonly_fields = ["created_at", "updated_at"]
    search_fields = ["sender__username", "body"]


@admin.register(DirectMessageReport)
class DirectMessageReportAdmin(admin.ModelAdmin):
    list_display = ["reporter", "message", "status", "handled_by", "created_at"]
    list_filter = ["status"]
    raw_id_fields = ["reporter", "message", "handled_by"]
    readonly_fields = ["created_at", "updated_at"]
