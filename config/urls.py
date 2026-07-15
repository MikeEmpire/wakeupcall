from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.delivery.views import twilio_voice_action, twilio_voice_status
from config.views import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.scheduling.urls")),
    path("", include("apps.accounts.web_urls")),
    path("", include("apps.scheduling.web_urls")),
    path("health/", health, name="health"),
    path(
        "twilio/voice/status/",
        twilio_voice_status,
        name="twilio-voice-status",
    ),
    path(
        "twilio/voice/action/",
        twilio_voice_action,
        name="twilio-voice-action",
    ),
]
