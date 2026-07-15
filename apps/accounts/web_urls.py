from django.urls import path

from apps.accounts import web_views

app_name = "accounts_web"

urlpatterns = [
    path("phones/", web_views.phone_list, name="phone-list"),
    path("phones/new/", web_views.phone_enroll, name="phone-enroll"),
    path(
        "phones/<int:phone_number_id>/verify/",
        web_views.phone_verify,
        name="phone-verify",
    ),
    path(
        "phones/<int:phone_number_id>/verification/start/",
        web_views.phone_verification_start,
        name="phone-verification-start",
    ),
]
