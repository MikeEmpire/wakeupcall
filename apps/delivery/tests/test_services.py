from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.delivery.gateways import DeliveryResult
from apps.delivery.models import DeliveryAttempt
from apps.delivery.services import EventNotDue, deliver_scheduled_event
from apps.scheduling.models import ScheduledEvent
from apps.weather.providers import CurrentWeather


@pytest.fixture
def due_event(db):
    user = get_user_model().objects.create_user(username="service-user")
    phone = PhoneNumber.objects.create(
        user=user,
        number="+14155552671",
        verified_at=timezone.now(),
    )
    return ScheduledEvent.objects.create(
        user=user,
        phone_number=phone,
        zip_code="94107",
        scheduled_for=timezone.now() - timedelta(minutes=1),
        channel=ScheduledEvent.Channel.SMS,
        is_demo=True,
    )


@pytest.fixture
def weather_provider():
    provider = Mock()
    provider.get_current_weather.return_value = CurrentWeather(
        location="San Francisco",
        temperature_f=61.4,
        condition="partly cloudy skies",
        observed_at=timezone.now(),
    )
    return provider


@pytest.mark.django_db
def test_demo_delivery_is_suppressed_and_audited(due_event, weather_provider):
    real_sender = Mock()
    demo_sender = Mock()
    demo_sender.send.return_value = DeliveryResult()

    attempt = deliver_scheduled_event(
        due_event.id,
        weather_provider=weather_provider,
        message_sender=real_sender,
        demo_sender=demo_sender,
    )

    due_event.refresh_from_db()
    assert due_event.status == ScheduledEvent.Status.SUPPRESSED
    assert attempt.status == DeliveryAttempt.Status.SUPPRESSED
    assert attempt.weather_snapshot["location"] == "San Francisco"
    assert "61°F" in attempt.rendered_message
    demo_sender.send.assert_called_once()
    real_sender.send.assert_not_called()


@pytest.mark.django_db
def test_non_demo_delivery_records_provider_submission(due_event, weather_provider):
    due_event.is_demo = False
    due_event.save(update_fields=["is_demo"])
    sender = Mock()
    sender.send.return_value = DeliveryResult(provider_sid="SM123")

    attempt = deliver_scheduled_event(
        due_event.id,
        weather_provider=weather_provider,
        message_sender=sender,
    )

    due_event.refresh_from_db()
    assert due_event.status == ScheduledEvent.Status.SUBMITTED
    assert attempt.status == DeliveryAttempt.Status.SUBMITTED
    assert attempt.provider_sid == "SM123"


@pytest.mark.django_db
def test_future_event_is_not_processed(due_event, weather_provider):
    due_event.scheduled_for = timezone.now() + timedelta(hours=1)
    due_event.save(update_fields=["scheduled_for"])

    with pytest.raises(EventNotDue):
        deliver_scheduled_event(due_event.id, weather_provider=weather_provider)

    assert not DeliveryAttempt.objects.filter(event=due_event).exists()


@pytest.mark.django_db
def test_provider_failure_is_recorded(due_event):
    weather_provider = Mock()
    weather_provider.get_current_weather.side_effect = RuntimeError(
        "weather unavailable"
    )

    with pytest.raises(RuntimeError, match="weather unavailable"):
        deliver_scheduled_event(due_event.id, weather_provider=weather_provider)

    due_event.refresh_from_db()
    attempt = due_event.delivery_attempts.get()
    assert due_event.status == ScheduledEvent.Status.FAILED
    assert attempt.status == DeliveryAttempt.Status.FAILED
    assert attempt.error_code == "RuntimeError"


@pytest.mark.django_db
def test_completed_event_is_idempotent(due_event, weather_provider):
    demo_sender = Mock()
    demo_sender.send.return_value = DeliveryResult()
    first_attempt = deliver_scheduled_event(
        due_event.id,
        weather_provider=weather_provider,
        demo_sender=demo_sender,
    )

    second_attempt = deliver_scheduled_event(
        due_event.id,
        weather_provider=weather_provider,
        demo_sender=demo_sender,
    )

    assert second_attempt.id == first_attempt.id
    demo_sender.send.assert_called_once()


@pytest.mark.django_db
def test_cancelled_event_cannot_be_delivered(due_event, weather_provider):
    due_event.transition_to(ScheduledEvent.Status.CANCELLED)
    due_event.save(update_fields=["status", "completed_at"])

    with pytest.raises(ValidationError, match="cannot be delivered"):
        deliver_scheduled_event(due_event.id, weather_provider=weather_provider)
