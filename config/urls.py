from django.contrib import admin
from django.urls import include, path

from apps.delivery.views import twilio_voice_status
from config.views import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.scheduling.urls")),
    path("health/", health, name="health"),
    path(
        "twilio/voice/status/",
        twilio_voice_status,
        name="twilio-voice-status",
    ),
]
