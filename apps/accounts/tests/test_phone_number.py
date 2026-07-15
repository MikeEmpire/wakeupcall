import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import PhoneNumber


@pytest.mark.django_db
def test_phone_number_tracks_verification():
    user = get_user_model().objects.create_user(username="phone-user")
    phone = PhoneNumber(
        user=user,
        number="+14155552671",
        verified_at=timezone.now(),
    )

    phone.full_clean()

    assert phone.is_verified is True


@pytest.mark.django_db
def test_phone_number_requires_e164_format():
    user = get_user_model().objects.create_user(username="phone-user")
    phone = PhoneNumber(user=user, number="415-555-2671")

    with pytest.raises(ValidationError, match="E.164"):
        phone.full_clean()
