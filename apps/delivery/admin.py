from django.contrib import admin

from .models import DeliveryAttempt


@admin.register(DeliveryAttempt)
class DeliveryAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "event",
        "attempt_number",
        "status",
        "provider_sid",
        "started_at",
    )
    list_filter = ("status",)
    search_fields = ("event__id", "provider_sid", "event__phone_number__number")
    list_select_related = ("event", "event__phone_number")
    readonly_fields = (
        "event",
        "attempt_number",
        "status",
        "rendered_message",
        "weather_snapshot",
        "provider_sid",
        "error_code",
        "error_message",
        "started_at",
        "completed_at",
    )

    def has_add_permission(self, request):
        return False
