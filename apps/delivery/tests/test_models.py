from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.delivery.models import DeliveryAttempt
from apps.scheduling.models import ScheduledEvent


@pytest.fixture
def scheduled_event(db):
    user = get_user_model().objects.create_user(username="delivery-user")
    phone = PhoneNumber.objects.create(
        user=user,
        number="+14155552671",
        verified_at=timezone.now(),
    )
    return ScheduledEvent.objects.create(
        user=user,
        phone_number=phone,
        zip_code="94107",
        scheduled_for=timezone.now() + timedelta(hours=1),
        channel=ScheduledEvent.Channel.SMS,
    )


@pytest.mark.django_db
def test_attempt_records_terminal_transition(scheduled_event):
    attempt = DeliveryAttempt.objects.create(event=scheduled_event, attempt_number=1)

    attempt.transition_to(DeliveryAttempt.Status.SUPPRESSED)

    assert attempt.status == DeliveryAttempt.Status.SUPPRESSED
    assert attempt.completed_at is not None


@pytest.mark.django_db
def test_attempt_rejects_second_terminal_transition(scheduled_event):
    attempt = DeliveryAttempt.objects.create(event=scheduled_event, attempt_number=1)
    attempt.transition_to(DeliveryAttempt.Status.FAILED)

    with pytest.raises(ValidationError, match="Cannot transition"):
        attempt.transition_to(DeliveryAttempt.Status.SUBMITTED)


@pytest.mark.django_db
def test_attempt_applies_newer_provider_status(scheduled_event):
    attempt = DeliveryAttempt.objects.create(event=scheduled_event, attempt_number=1)

    applied = attempt.apply_provider_status(
        DeliveryAttempt.ProviderStatus.RINGING,
        sequence_number=1,
    )

    assert applied is True
    assert attempt.provider_status == DeliveryAttempt.ProviderStatus.RINGING
    assert attempt.provider_status_sequence == 1
    assert attempt.provider_status_updated_at is not None


@pytest.mark.django_db
def test_attempt_ignores_duplicate_and_out_of_order_provider_statuses(
    scheduled_event,
):
    attempt = DeliveryAttempt.objects.create(event=scheduled_event, attempt_number=1)
    attempt.apply_provider_status(
        DeliveryAttempt.ProviderStatus.RINGING,
        sequence_number=2,
    )

    duplicate = attempt.apply_provider_status(
        DeliveryAttempt.ProviderStatus.RINGING,
        sequence_number=2,
    )
    older = attempt.apply_provider_status(
        DeliveryAttempt.ProviderStatus.INITIATED,
        sequence_number=1,
    )

    assert duplicate is False
    assert older is False
    assert attempt.provider_status == DeliveryAttempt.ProviderStatus.RINGING


@pytest.mark.django_db
def test_attempt_does_not_regress_terminal_provider_status(scheduled_event):
    attempt = DeliveryAttempt.objects.create(event=scheduled_event, attempt_number=1)
    attempt.apply_provider_status(
        DeliveryAttempt.ProviderStatus.COMPLETED,
        sequence_number=3,
    )

    applied = attempt.apply_provider_status(
        DeliveryAttempt.ProviderStatus.RINGING,
        sequence_number=4,
    )

    assert applied is False
    assert attempt.provider_status == DeliveryAttempt.ProviderStatus.COMPLETED
