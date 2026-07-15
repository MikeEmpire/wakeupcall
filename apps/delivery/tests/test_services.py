from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.delivery.gateways import DeliveryResult
from apps.delivery.models import DeliveryAttempt
from apps.delivery.services import (
    DeliveryAttemptNotFound,
    EventNotDue,
    claim_due_delivery_batch,
    deliver_scheduled_event,
    dispatch_due_events,
    record_voice_status_callback,
)
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


@pytest.mark.django_db
def test_voice_callback_records_provider_outcome_without_changing_event_status(
    due_event,
    weather_provider,
):
    due_event.channel = ScheduledEvent.Channel.VOICE
    due_event.is_demo = False
    due_event.save(update_fields=["channel", "is_demo"])
    sender = Mock()
    provider_sid = "CA" + "0" * 32
    sender.send.return_value = DeliveryResult(provider_sid=provider_sid)
    attempt = deliver_scheduled_event(
        due_event.id,
        weather_provider=weather_provider,
        message_sender=sender,
    )

    update = record_voice_status_callback(
        provider_sid=provider_sid,
        provider_status=DeliveryAttempt.ProviderStatus.COMPLETED,
        sequence_number=3,
    )

    attempt.refresh_from_db()
    due_event.refresh_from_db()
    assert update.applied is True
    assert attempt.provider_status == DeliveryAttempt.ProviderStatus.COMPLETED
    assert attempt.provider_status_sequence == 3
    assert due_event.status == ScheduledEvent.Status.SUBMITTED


@pytest.mark.django_db
def test_voice_callback_duplicate_is_idempotent(due_event, weather_provider):
    due_event.channel = ScheduledEvent.Channel.VOICE
    due_event.is_demo = False
    due_event.save(update_fields=["channel", "is_demo"])
    provider_sid = "CA" + "1" * 32
    sender = Mock()
    sender.send.return_value = DeliveryResult(provider_sid=provider_sid)
    deliver_scheduled_event(
        due_event.id,
        weather_provider=weather_provider,
        message_sender=sender,
    )
    record_voice_status_callback(
        provider_sid=provider_sid,
        provider_status=DeliveryAttempt.ProviderStatus.RINGING,
        sequence_number=1,
    )

    duplicate = record_voice_status_callback(
        provider_sid=provider_sid,
        provider_status=DeliveryAttempt.ProviderStatus.RINGING,
        sequence_number=1,
    )

    assert duplicate.applied is False


@pytest.mark.django_db
def test_voice_callback_rejects_unknown_attempt():
    with pytest.raises(DeliveryAttemptNotFound):
        record_voice_status_callback(
            provider_sid="CA" + "2" * 32,
            provider_status=DeliveryAttempt.ProviderStatus.COMPLETED,
            sequence_number=3,
        )


@pytest.mark.django_db
def test_dispatcher_processes_due_demo_without_real_sender(due_event, weather_provider):
    real_sender = Mock()
    demo_sender = Mock()
    demo_sender.send.return_value = DeliveryResult()

    result = dispatch_due_events(
        weather_provider=weather_provider,
        message_sender=real_sender,
        demo_sender=demo_sender,
        now=timezone.now(),
    )

    due_event.refresh_from_db()
    assert result.selected_count == 1
    assert result.delivered_count == 1
    assert result.missed_count == 0
    assert result.failed_count == 0
    assert due_event.status == ScheduledEvent.Status.SUPPRESSED
    real_sender.send.assert_not_called()
    demo_sender.send.assert_called_once()


@pytest.mark.django_db
def test_dispatcher_excludes_real_events_by_default(due_event, weather_provider):
    due_event.is_demo = False
    due_event.save(update_fields=["is_demo"])

    result = dispatch_due_events(
        weather_provider=weather_provider,
        message_sender=Mock(),
        now=timezone.now(),
    )

    due_event.refresh_from_db()
    assert result.selected_count == 0
    assert due_event.status == ScheduledEvent.Status.SCHEDULED
    assert not due_event.delivery_attempts.exists()


@pytest.mark.django_db
def test_dispatcher_can_explicitly_submit_real_event(due_event, weather_provider):
    due_event.is_demo = False
    due_event.save(update_fields=["is_demo"])
    sender = Mock()
    sender.send.return_value = DeliveryResult(provider_sid="SM123")

    result = dispatch_due_events(
        weather_provider=weather_provider,
        message_sender=sender,
        now=timezone.now(),
        include_real=True,
    )

    due_event.refresh_from_db()
    attempt = due_event.delivery_attempts.get()
    assert result.delivered_count == 1
    assert due_event.status == ScheduledEvent.Status.SUBMITTED
    assert attempt.provider_sid == "SM123"


@pytest.mark.django_db
def test_dispatcher_marks_event_outside_grace_window_missed(
    due_event, weather_provider
):
    now = timezone.now()
    due_event.scheduled_for = now - timedelta(minutes=16)
    due_event.save(update_fields=["scheduled_for"])
    demo_sender = Mock()

    result = dispatch_due_events(
        weather_provider=weather_provider,
        demo_sender=demo_sender,
        now=now,
        grace_period=timedelta(minutes=15),
    )

    due_event.refresh_from_db()
    attempt = due_event.delivery_attempts.get()
    assert result.selected_count == 1
    assert result.missed_count == 1
    assert result.delivered_count == 0
    assert due_event.status == ScheduledEvent.Status.FAILED
    assert attempt.error_code == "MissedDeliveryWindow"
    weather_provider.get_current_weather.assert_not_called()
    demo_sender.send.assert_not_called()


@pytest.mark.django_db
def test_dispatcher_bounds_batch_oldest_first_and_excludes_future(
    due_event, weather_provider
):
    now = timezone.now()
    due_event.scheduled_for = now - timedelta(minutes=3)
    due_event.save(update_fields=["scheduled_for"])
    newer = ScheduledEvent.objects.create(
        user=due_event.user,
        phone_number=due_event.phone_number,
        zip_code="94107",
        scheduled_for=now - timedelta(minutes=2),
        channel=ScheduledEvent.Channel.SMS,
        is_demo=True,
    )
    future = ScheduledEvent.objects.create(
        user=due_event.user,
        phone_number=due_event.phone_number,
        zip_code="94107",
        scheduled_for=now + timedelta(minutes=2),
        channel=ScheduledEvent.Channel.SMS,
        is_demo=True,
    )

    result = dispatch_due_events(
        weather_provider=weather_provider,
        now=now,
        batch_size=1,
    )

    due_event.refresh_from_db()
    newer.refresh_from_db()
    future.refresh_from_db()
    assert result.selected_count == 1
    assert due_event.status == ScheduledEvent.Status.SUPPRESSED
    assert newer.status == ScheduledEvent.Status.SCHEDULED
    assert future.status == ScheduledEvent.Status.SCHEDULED


@pytest.mark.django_db
def test_dispatcher_continues_after_one_delivery_fails(due_event, weather_provider):
    second = ScheduledEvent.objects.create(
        user=due_event.user,
        phone_number=due_event.phone_number,
        zip_code="94107",
        scheduled_for=due_event.scheduled_for + timedelta(seconds=1),
        channel=ScheduledEvent.Channel.SMS,
        is_demo=True,
    )
    demo_sender = Mock()
    demo_sender.send.side_effect = [RuntimeError("provider failed"), DeliveryResult()]

    result = dispatch_due_events(
        weather_provider=weather_provider,
        demo_sender=demo_sender,
        now=timezone.now(),
    )

    due_event.refresh_from_db()
    second.refresh_from_db()
    assert result.failed_count == 1
    assert result.delivered_count == 1
    assert due_event.status == ScheduledEvent.Status.FAILED
    assert second.status == ScheduledEvent.Status.SUPPRESSED


@pytest.mark.django_db
def test_dispatcher_calls_gateways_outside_atomic_transaction(
    due_event, weather_provider
):
    def assert_not_atomic(*args, **kwargs):
        assert connection.in_atomic_block is False
        return CurrentWeather(
            location="San Francisco",
            temperature_f=61.4,
            condition="clear skies",
            observed_at=timezone.now(),
        )

    weather_provider.get_current_weather.side_effect = assert_not_atomic
    demo_sender = Mock()
    demo_sender.send.side_effect = lambda **kwargs: (
        DeliveryResult()
        if not connection.in_atomic_block
        else pytest.fail("sender called inside an atomic transaction")
    )

    dispatch_due_events(
        weather_provider=weather_provider,
        demo_sender=demo_sender,
        now=timezone.now(),
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("batch_size", "grace_period"),
    [(0, timedelta(minutes=15)), (101, timedelta(minutes=15)), (1, timedelta(-1))],
)
def test_claim_due_batch_rejects_invalid_bounds(batch_size, grace_period):
    with pytest.raises(ValueError):
        claim_due_delivery_batch(
            now=timezone.now(),
            batch_size=batch_size,
            grace_period=grace_period,
        )
