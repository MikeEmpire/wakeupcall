import re

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.accounts.verification import (
    PhoneVerificationGateway,
    VerificationResult,
    VerificationStatus,
)
from apps.accounts.verification_exceptions import (
    PhoneAlreadyVerified,
    PhoneNumberChanged,
    VerificationInputInvalid,
)

VERIFICATION_CODE_PATTERN = re.compile(r"^\d{4,10}$")


@transaction.atomic
def create_phone_number(*, user, number: str) -> PhoneNumber:
    phone_number = PhoneNumber(user=user, number=number)
    phone_number.full_clean(validate_unique=False, validate_constraints=False)
    try:
        phone_number.save()
    except IntegrityError as exc:
        raise ValidationError(
            {"number": "This phone number cannot be enrolled."}
        ) from exc
    return phone_number


def start_phone_verification(
    phone_number_id: int,
    *,
    gateway: PhoneVerificationGateway,
) -> VerificationResult:
    phone_number = PhoneNumber.objects.get(id=phone_number_id)
    if phone_number.is_verified:
        raise PhoneAlreadyVerified("This phone number is already verified.")

    return gateway.start_verification(phone_number.number)


def start_user_phone_verification(
    phone_number_id: int,
    *,
    user,
    gateway: PhoneVerificationGateway | None = None,
) -> VerificationResult:
    phone_number = PhoneNumber.objects.get(id=phone_number_id, user=user)
    if phone_number.is_verified:
        raise PhoneAlreadyVerified("This phone number is already verified.")
    if gateway is None:
        raise ValueError("A verification gateway is required.")

    return gateway.start_verification(phone_number.number)


def check_phone_verification(
    phone_number_id: int,
    code: str,
    *,
    gateway: PhoneVerificationGateway,
    verified_at=None,
) -> VerificationResult:
    if not isinstance(code, str) or not VERIFICATION_CODE_PATTERN.fullmatch(code):
        raise VerificationInputInvalid(
            "The verification code must contain 4 to 10 digits."
        )

    phone_number = PhoneNumber.objects.get(id=phone_number_id)
    if phone_number.is_verified:
        return VerificationResult(status=VerificationStatus.APPROVED)

    original_number = phone_number.number
    result = gateway.check_verification(original_number, code)
    if result.status != VerificationStatus.APPROVED:
        return result

    _mark_phone_verified(
        phone_number_id,
        expected_number=original_number,
        verified_at=verified_at or timezone.now(),
    )
    return result


def check_user_phone_verification(
    phone_number_id: int,
    code: str,
    *,
    user,
    gateway: PhoneVerificationGateway | None = None,
    verified_at=None,
) -> VerificationResult:
    if not isinstance(code, str) or not VERIFICATION_CODE_PATTERN.fullmatch(code):
        raise VerificationInputInvalid(
            "The verification code must contain 4 to 10 digits."
        )

    phone_number = PhoneNumber.objects.get(id=phone_number_id, user=user)
    if phone_number.is_verified:
        return VerificationResult(status=VerificationStatus.APPROVED)
    if gateway is None:
        raise ValueError("A verification gateway is required.")

    original_number = phone_number.number
    result = gateway.check_verification(original_number, code)
    if result.status != VerificationStatus.APPROVED:
        return result

    _mark_phone_verified(
        phone_number_id,
        expected_number=original_number,
        verified_at=verified_at or timezone.now(),
    )
    return result


@transaction.atomic
def _mark_phone_verified(
    phone_number_id: int,
    *,
    expected_number: str,
    verified_at,
):
    phone_number = PhoneNumber.objects.select_for_update().get(id=phone_number_id)
    if phone_number.number != expected_number:
        raise PhoneNumberChanged(
            "The phone number changed while verification was in progress."
        )
    if phone_number.verified_at is None:
        phone_number.verified_at = verified_at
        phone_number.save(update_fields=["verified_at", "updated_at"])
