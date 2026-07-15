from datetime import UTC, datetime, timedelta, timezone as datetime_timezone

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.scheduling.models import ScheduledEvent
from apps.scheduling.services import (
    cancel_scheduled_event,
    change_user_scheduled_event_channel,
    reschedule_user_scheduled_event,
)


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


@pytest.mark.django_db
def test_reschedule_user_event_normalizes_to_utc(scheduled_event):
    supplied = (datetime.now(UTC) + timedelta(hours=2)).astimezone(
        datetime_timezone(timedelta(hours=3))
    )

    result = reschedule_user_scheduled_event(
        scheduled_event.id,
        user=scheduled_event.user,
        scheduled_for=supplied,
    )

    assert result.scheduled_for == supplied
    assert result.scheduled_for.utcoffset() == timedelta(0)
    assert result.status == ScheduledEvent.Status.SCHEDULED


@pytest.mark.django_db
@pytest.mark.parametrize(
    "scheduled_for",
    [datetime.now() + timedelta(hours=2), timezone.now() - timedelta(seconds=1)],
)
def test_reschedule_user_event_rejects_invalid_time(scheduled_event, scheduled_for):
    original_time = scheduled_event.scheduled_for

    with pytest.raises(ValidationError):
        reschedule_user_scheduled_event(
            scheduled_event.id,
            user=scheduled_event.user,
            scheduled_for=scheduled_for,
        )

    scheduled_event.refresh_from_db()
    assert scheduled_event.scheduled_for == original_time


@pytest.mark.django_db
def test_change_user_event_channel(scheduled_event):
    result = change_user_scheduled_event_channel(
        scheduled_event.id,
        user=scheduled_event.user,
        channel=ScheduledEvent.Channel.VOICE,
    )

    assert result.channel == ScheduledEvent.Channel.VOICE
    assert result.status == ScheduledEvent.Status.SCHEDULED


@pytest.mark.django_db
def test_change_user_event_channel_rejects_invalid_choice(scheduled_event):
    with pytest.raises(ValidationError):
        change_user_scheduled_event_channel(
            scheduled_event.id,
            user=scheduled_event.user,
            channel="email",
        )

    scheduled_event.refresh_from_db()
    assert scheduled_event.channel == ScheduledEvent.Channel.SMS


@pytest.mark.django_db
def test_pending_changes_require_owned_scheduled_event(scheduled_event):
    other_user = get_user_model().objects.create_user(username="other-user")

    with pytest.raises(ScheduledEvent.DoesNotExist):
        change_user_scheduled_event_channel(
            scheduled_event.id,
            user=other_user,
            channel=ScheduledEvent.Channel.VOICE,
        )

    scheduled_event.transition_to(ScheduledEvent.Status.PROCESSING)
    scheduled_event.save(update_fields=["status", "processing_started_at"])
    with pytest.raises(ValidationError, match="cannot be rescheduled"):
        reschedule_user_scheduled_event(
            scheduled_event.id,
            user=scheduled_event.user,
            scheduled_for=timezone.now() + timedelta(hours=2),
        )
