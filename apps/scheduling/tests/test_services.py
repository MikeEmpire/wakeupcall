from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.scheduling.models import ScheduledEvent
from apps.scheduling.services import cancel_scheduled_event


@pytest.fixture
def scheduled_event(db):
    user = get_user_model().objects.create_user(username="cancel-user")
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
def test_cancel_scheduled_event_records_terminal_state(scheduled_event):
    cancelled_at = timezone.now()

    result = cancel_scheduled_event(scheduled_event.id, cancelled_at=cancelled_at)

    assert result.status == ScheduledEvent.Status.CANCELLED
    assert result.completed_at == cancelled_at


@pytest.mark.django_db
def test_cancel_scheduled_event_rejects_non_scheduled_state(scheduled_event):
    scheduled_event.transition_to(ScheduledEvent.Status.PROCESSING)
    scheduled_event.save(update_fields=["status", "processing_started_at"])

    with pytest.raises(ValidationError, match="cannot be cancelled"):
        cancel_scheduled_event(scheduled_event.id)
