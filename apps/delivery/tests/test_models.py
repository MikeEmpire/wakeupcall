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
