from django.contrib import admin

from messaging.models import Conversation, DirectMessage


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
