from django.contrib import admin

from .models import ScheduledEvent


@admin.register(ScheduledEvent)
class ScheduledEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "channel",
        "scheduled_for",
        "status",
        "is_demo",
    )
    list_filter = ("status", "channel", "is_demo")
    search_fields = ("user__username", "phone_number__number", "zip_code")
    list_select_related = ("user", "phone_number")
    readonly_fields = (
        "status",
        "processing_started_at",
        "completed_at",
        "created_at",
        "updated_at",
    )
