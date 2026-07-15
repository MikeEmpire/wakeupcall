from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.accounts.services import (
    check_phone_verification,
    start_phone_verification,
)
from apps.accounts.verification import VerificationResult, VerificationStatus
from apps.accounts.verification_exceptions import (
    PhoneAlreadyVerified,
    PhoneNumberChanged,
    VerificationInputInvalid,
)


@pytest.fixture
def phone_number(db):
    user = get_user_model().objects.create_user(username="verification-user")
    return PhoneNumber.objects.create(user=user, number="+14155552671")


@pytest.mark.django_db
def test_starts_verification_for_unverified_phone(phone_number):
    gateway = Mock()
    gateway.start_verification.return_value = VerificationResult(
        status=VerificationStatus.PENDING,
        provider_sid="VE123",
    )

    result = start_phone_verification(phone_number.id, gateway=gateway)

    assert result.status == VerificationStatus.PENDING
    gateway.start_verification.assert_called_once_with("+14155552671")


@pytest.mark.django_db
def test_does_not_restart_verification_for_verified_phone(phone_number):
    phone_number.verified_at = timezone.now()
    phone_number.save(update_fields=["verified_at"])
    gateway = Mock()

    with pytest.raises(PhoneAlreadyVerified):
        start_phone_verification(phone_number.id, gateway=gateway)

    gateway.start_verification.assert_not_called()


@pytest.mark.django_db
def test_approved_check_marks_phone_verified(phone_number):
    gateway = Mock()
    gateway.check_verification.return_value = VerificationResult(
        status=VerificationStatus.APPROVED,
        provider_sid="VE123",
    )
    verified_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

    result = check_phone_verification(
        phone_number.id,
        "123456",
        gateway=gateway,
        verified_at=verified_at,
    )

    phone_number.refresh_from_db()
    assert result.status == VerificationStatus.APPROVED
    assert phone_number.verified_at == verified_at
    gateway.check_verification.assert_called_once_with("+14155552671", "123456")


@pytest.mark.django_db
def test_rejected_check_leaves_phone_unverified(phone_number):
    gateway = Mock()
    gateway.check_verification.return_value = VerificationResult(
        status=VerificationStatus.REJECTED,
        provider_sid="VE123",
    )

    result = check_phone_verification(
        phone_number.id,
        "123456",
        gateway=gateway,
    )

    phone_number.refresh_from_db()
    assert result.status == VerificationStatus.REJECTED
    assert phone_number.verified_at is None


@pytest.mark.django_db
@pytest.mark.parametrize("code", [None, "", "123", "12345678901", "12A4", "12 34"])
def test_invalid_code_is_rejected_before_provider_call(phone_number, code):
    gateway = Mock()

    with pytest.raises(VerificationInputInvalid, match="4 to 10 digits"):
        check_phone_verification(phone_number.id, code, gateway=gateway)

    gateway.check_verification.assert_not_called()


@pytest.mark.django_db
def test_check_is_idempotent_for_verified_phone(phone_number):
    phone_number.verified_at = timezone.now()
    phone_number.save(update_fields=["verified_at"])
    gateway = Mock()

    result = check_phone_verification(
        phone_number.id,
        "123456",
        gateway=gateway,
    )

    assert result.status == VerificationStatus.APPROVED
    gateway.check_verification.assert_not_called()


@pytest.mark.django_db
def test_changed_phone_is_not_marked_verified(phone_number):
    gateway = Mock()
    gateway.check_verification.return_value = VerificationResult(
        status=VerificationStatus.APPROVED,
        provider_sid="VE123",
    )

    def change_phone(*args, **kwargs):
        PhoneNumber.objects.filter(id=phone_number.id).update(number="+14155550000")
        return gateway.check_verification.return_value

    gateway.check_verification.side_effect = change_phone

    with pytest.raises(PhoneNumberChanged):
        check_phone_verification(
            phone_number.id,
            "123456",
            gateway=gateway,
        )

    phone_number.refresh_from_db()
    assert phone_number.verified_at is None
