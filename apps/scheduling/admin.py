from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import ScheduledEvent
from .services import cancel_scheduled_event


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
    actions = ("cancel_selected_scheduled_events",)

    @admin.action(description="Cancel selected scheduled events")
    def cancel_selected_scheduled_events(self, request, queryset):
        cancelled_count = 0
        event_ids = queryset.filter(
            status=ScheduledEvent.Status.SCHEDULED
        ).values_list("id", flat=True)
        for event_id in event_ids:
            try:
                cancel_scheduled_event(event_id)
            except (ScheduledEvent.DoesNotExist, ValidationError):
                continue
            cancelled_count += 1
        self.message_user(request, f"Cancelled {cancelled_count} scheduled event(s).")
