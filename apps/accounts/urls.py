from django.urls import path

from apps.accounts.views import (
    PhoneNumberListCreateView,
    PhoneVerificationCheckView,
    PhoneVerificationStartView,
)

app_name = "accounts"

urlpatterns = [
    path("phones/", PhoneNumberListCreateView.as_view(), name="phone-list"),
    path(
        "phones/<int:phone_number_id>/verification/start/",
        PhoneVerificationStartView.as_view(),
        name="phone-verification-start",
    ),
    path(
        "phones/<int:phone_number_id>/verification/check/",
        PhoneVerificationCheckView.as_view(),
        name="phone-verification-check",
    ),
]
