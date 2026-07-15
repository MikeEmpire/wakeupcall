from django.contrib import admin

from .models import DeliveryAttempt


@admin.register(DeliveryAttempt)
class DeliveryAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "event",
        "attempt_number",
        "status",
        "provider_status",
        "provider_sid",
        "started_at",
    )
    list_filter = ("status", "provider_status")
    search_fields = ("event__id", "provider_sid", "event__phone_number__number")
    list_select_related = ("event", "event__phone_number")
    readonly_fields = (
        "event",
        "attempt_number",
        "status",
        "rendered_message",
        "weather_snapshot",
        "provider_sid",
        "provider_status",
        "provider_status_sequence",
        "provider_status_updated_at",
        "voice_action_digit",
        "voice_action_result",
        "voice_action_target_event_id",
        "voice_action_completed_at",
        "error_code",
        "error_message",
        "started_at",
        "completed_at",
    )

    def has_add_permission(self, request):
        return False
