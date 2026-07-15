from django.contrib import admin
from django.urls import path

from apps.delivery.views import twilio_voice_status
from config.views import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path(
        "twilio/voice/status/",
        twilio_voice_status,
        name="twilio-voice-status",
    ),
]
