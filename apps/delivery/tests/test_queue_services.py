from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.delivery.exceptions import DeliveryProviderTimeout
from apps.delivery.gateways import DeliveryResult
from apps.delivery.models import DeliveryAttempt
from apps.delivery.queue_services import (
    process_queue_batch,
    publish_due_event_messages,
)
from apps.delivery.queueing import QueueEnvelope, ReceivedQueueMessage
from apps.delivery.services import QueueDeliveryAction, process_queued_delivery
from apps.scheduling.models import ScheduledEvent
from apps.weather.exceptions import (
    WeatherAuthenticationError,
    WeatherProviderTimeout,
)
from apps.weather.providers import CurrentWeather


@pytest.fixture
def due_event(db):
    user = get_user_model().objects.create_user(username="queue-user")
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
        temperature_f=62.0,
        condition="clear skies",
        observed_at=timezone.now(),
    )
    return provider


def message_for(envelope, *, receive_count=1, message_id="message-1"):
    return ReceivedQueueMessage(
        body=envelope.to_json(),
        receipt_handle="receipt-secret",
        receive_count=receive_count,
        message_id=message_id,
    )


@pytest.mark.django_db
def test_publication_is_bounded_demo_only_and_does_not_claim(due_event):
    real_event = ScheduledEvent.objects.create(
        user=due_event.user,
        phone_number=due_event.phone_number,
        zip_code="94107",
        scheduled_for=due_event.scheduled_for - timedelta(seconds=1),
        channel=ScheduledEvent.Channel.SMS,
        is_demo=False,
    )
    queue = Mock()

    result = publish_due_event_messages(queue=queue, batch_size=1)

    due_event.refresh_from_db()
    real_event.refresh_from_db()
    assert result.selected_count == 1
    assert result.published_count == 1
    queue.publish.assert_called_once_with(QueueEnvelope.deliver_event(due_event.id))
    assert due_event.status == ScheduledEvent.Status.SCHEDULED
    assert real_event.status == ScheduledEvent.Status.SCHEDULED
    assert DeliveryAttempt.objects.count() == 0


@pytest.mark.django_db
def test_repeated_tick_can_publish_duplicate_identifier_safely(due_event):
    queue = Mock()

    publish_due_event_messages(queue=queue, batch_size=10)
    publish_due_event_messages(queue=queue, batch_size=10)

    assert queue.publish.call_count == 2
    assert queue.publish.call_args_list[0] == queue.publish.call_args_list[1]


@pytest.mark.django_db
def test_tick_expands_to_delivery_message_and_is_acknowledged(
    due_event, weather_provider
):
    queue = Mock()
    tick = message_for(QueueEnvelope.dispatch_tick())

    result = process_queue_batch(
        queue=queue,
        messages=(tick,),
        weather_provider=weather_provider,
        message_sender=None,
        batch_size=10,
        max_receive_count=3,
        retry_base_seconds=30,
        retry_max_seconds=300,
    )

    queue.publish.assert_called_once_with(QueueEnvelope.deliver_event(due_event.id))
    queue.delete.assert_called_once_with(tick)
    assert result.published_count == 1
    assert result.acknowledged_count == 1


@pytest.mark.django_db
def test_demo_delivery_message_is_suppressed_and_acknowledged(
    due_event, weather_provider
):
    queue = Mock()
    message = message_for(QueueEnvelope.deliver_event(due_event.id))

    result = process_queue_batch(
        queue=queue,
        messages=(message,),
        weather_provider=weather_provider,
        message_sender=Mock(),
        batch_size=10,
        max_receive_count=3,
        retry_base_seconds=30,
        retry_max_seconds=300,
    )

    due_event.refresh_from_db()
    assert due_event.status == ScheduledEvent.Status.SUPPRESSED
    assert due_event.delivery_attempts.get().status == DeliveryAttempt.Status.SUPPRESSED
    queue.delete.assert_called_once_with(message)
    assert result.acknowledged_count == 1


@pytest.mark.django_db
def test_queued_delivery_calls_providers_outside_atomic_transaction(due_event):
    weather_provider = Mock()

    def get_weather(zip_code):
        assert connection.in_atomic_block is False
        return CurrentWeather(
            location="San Francisco",
            temperature_f=62.0,
            condition="clear skies",
            observed_at=timezone.now(),
        )

    weather_provider.get_current_weather.side_effect = get_weather
    demo_sender = Mock()

    def send(**kwargs):
        assert connection.in_atomic_block is False
        return DeliveryResult()

    demo_sender.send.side_effect = send

    process_queued_delivery(
        due_event.id,
        receive_count=1,
        max_receive_count=3,
        weather_provider=weather_provider,
        demo_sender=demo_sender,
    )


@pytest.mark.django_db
def test_duplicate_delivery_message_is_acknowledged_without_second_send(
    due_event, weather_provider
):
    demo_sender = Mock()
    demo_sender.send.return_value = DeliveryResult()

    first = process_queued_delivery(
        due_event.id,
        receive_count=1,
        max_receive_count=3,
        weather_provider=weather_provider,
        demo_sender=demo_sender,
    )
    duplicate = process_queued_delivery(
        due_event.id,
        receive_count=1,
        max_receive_count=3,
        weather_provider=weather_provider,
        demo_sender=demo_sender,
    )

    assert first == QueueDeliveryAction.ACKNOWLEDGE
    assert duplicate == QueueDeliveryAction.ACKNOWLEDGE
    demo_sender.send.assert_called_once()
    assert due_event.delivery_attempts.count() == 1


@pytest.mark.django_db
def test_retryable_weather_failure_releases_event_for_new_attempt(due_event):
    weather_provider = Mock()
    weather_provider.get_current_weather.side_effect = WeatherProviderTimeout(
        "safe weather timeout"
    )

    action = process_queued_delivery(
        due_event.id,
        receive_count=1,
        max_receive_count=3,
        weather_provider=weather_provider,
    )

    due_event.refresh_from_db()
    attempt = due_event.delivery_attempts.get()
    assert action == QueueDeliveryAction.RETRY
    assert due_event.status == ScheduledEvent.Status.PROCESSING
    assert due_event.processing_started_at is not None
    assert attempt.status == DeliveryAttempt.Status.FAILED
    assert attempt.error_code == "QueueRetryable:WeatherProviderTimeout"


@pytest.mark.django_db
def test_retry_creates_new_attempt_and_can_succeed(due_event, weather_provider):
    failing_weather = Mock()
    failing_weather.get_current_weather.side_effect = WeatherProviderTimeout("timeout")
    process_queued_delivery(
        due_event.id,
        receive_count=1,
        max_receive_count=3,
        weather_provider=failing_weather,
    )

    action = process_queued_delivery(
        due_event.id,
        receive_count=2,
        max_receive_count=3,
        weather_provider=weather_provider,
    )

    due_event.refresh_from_db()
    assert action == QueueDeliveryAction.ACKNOWLEDGE
    assert due_event.status == ScheduledEvent.Status.SUPPRESSED
    assert list(
        due_event.delivery_attempts.values_list("attempt_number", "status")
    ) == [
        (1, DeliveryAttempt.Status.FAILED),
        (2, DeliveryAttempt.Status.SUPPRESSED),
    ]


@pytest.mark.django_db
def test_retry_exhaustion_fails_event_and_retains_message_for_dlq(due_event):
    weather_provider = Mock()
    weather_provider.get_current_weather.side_effect = WeatherProviderTimeout("timeout")

    action = process_queued_delivery(
        due_event.id,
        receive_count=3,
        max_receive_count=3,
        weather_provider=weather_provider,
    )
    repeated_action = process_queued_delivery(
        due_event.id,
        receive_count=4,
        max_receive_count=3,
        weather_provider=weather_provider,
    )

    due_event.refresh_from_db()
    attempt = due_event.delivery_attempts.get()
    assert action == QueueDeliveryAction.RETRY
    assert repeated_action == QueueDeliveryAction.RETRY
    assert due_event.status == ScheduledEvent.Status.FAILED
    assert attempt.error_code == "RetryExhausted:WeatherProviderTimeout"
    assert weather_provider.get_current_weather.call_count == 1


@pytest.mark.django_db
def test_permanent_weather_failure_is_terminal_and_acknowledged(due_event):
    weather_provider = Mock()
    weather_provider.get_current_weather.side_effect = WeatherAuthenticationError(
        "credentials invalid"
    )

    action = process_queued_delivery(
        due_event.id,
        receive_count=1,
        max_receive_count=3,
        weather_provider=weather_provider,
    )

    due_event.refresh_from_db()
    assert action == QueueDeliveryAction.ACKNOWLEDGE
    assert due_event.status == ScheduledEvent.Status.FAILED


@pytest.mark.django_db
def test_sender_timeout_is_terminal_to_avoid_ambiguous_replay(
    due_event, weather_provider
):
    due_event.is_demo = False
    due_event.save(update_fields=["is_demo"])
    sender = Mock()
    sender.send.side_effect = DeliveryProviderTimeout("ambiguous timeout")

    action = process_queued_delivery(
        due_event.id,
        receive_count=1,
        max_receive_count=3,
        weather_provider=weather_provider,
        message_sender=sender,
        allow_real=True,
    )

    due_event.refresh_from_db()
    assert action == QueueDeliveryAction.ACKNOWLEDGE
    assert due_event.status == ScheduledEvent.Status.FAILED
    assert due_event.delivery_attempts.get().error_code == "DeliveryProviderTimeout"


@pytest.mark.django_db
def test_explicit_real_queue_delivery_records_provider_submission(
    due_event, weather_provider
):
    due_event.is_demo = False
    due_event.save(update_fields=["is_demo"])
    sender = Mock()
    sender.send.return_value = DeliveryResult(provider_sid="SM123")

    action = process_queued_delivery(
        due_event.id,
        receive_count=1,
        max_receive_count=3,
        weather_provider=weather_provider,
        message_sender=sender,
        allow_real=True,
    )

    due_event.refresh_from_db()
    attempt = due_event.delivery_attempts.get()
    assert action == QueueDeliveryAction.ACKNOWLEDGE
    assert due_event.status == ScheduledEvent.Status.SUBMITTED
    assert attempt.provider_sid == "SM123"


@pytest.mark.django_db
def test_real_event_message_is_safe_noop_without_explicit_worker_gate(
    due_event, weather_provider
):
    due_event.is_demo = False
    due_event.save(update_fields=["is_demo"])
    sender = Mock()

    action = process_queued_delivery(
        due_event.id,
        receive_count=1,
        max_receive_count=3,
        weather_provider=weather_provider,
        message_sender=sender,
        allow_real=False,
    )

    due_event.refresh_from_db()
    assert action == QueueDeliveryAction.ACKNOWLEDGE
    assert due_event.status == ScheduledEvent.Status.SCHEDULED
    assert not due_event.delivery_attempts.exists()
    weather_provider.get_current_weather.assert_not_called()
    sender.send.assert_not_called()


@pytest.mark.django_db
def test_missed_event_is_failed_without_provider_calls(due_event, weather_provider):
    due_event.scheduled_for = timezone.now() - timedelta(minutes=16)
    due_event.save(update_fields=["scheduled_for"])

    action = process_queued_delivery(
        due_event.id,
        receive_count=1,
        max_receive_count=3,
        weather_provider=weather_provider,
        grace_period=timedelta(minutes=15),
    )

    due_event.refresh_from_db()
    assert action == QueueDeliveryAction.ACKNOWLEDGE
    assert due_event.status == ScheduledEvent.Status.FAILED
    assert due_event.delivery_attempts.get().error_code == "MissedDeliveryWindow"
    weather_provider.get_current_weather.assert_not_called()


@pytest.mark.django_db
def test_malformed_message_is_not_logged_or_deleted(due_event, weather_provider, caplog):
    queue = Mock()
    sensitive_body = '{"phone":"+14155552671","message":"secret body"}'
    message = ReceivedQueueMessage(
        body=sensitive_body,
        receipt_handle="receipt-secret",
        receive_count=2,
        message_id="message-1",
    )
    caplog.set_level("WARNING", logger="apps.delivery.queue_services")

    result = process_queue_batch(
        queue=queue,
        messages=(message,),
        weather_provider=weather_provider,
        message_sender=None,
        batch_size=10,
        max_receive_count=3,
        retry_base_seconds=30,
        retry_max_seconds=300,
    )

    queue.delete.assert_not_called()
    queue.change_visibility.assert_called_once_with(message, visibility_timeout=60)
    assert result.malformed_count == 1
    assert sensitive_body not in caplog.text
    assert "+14155552671" not in caplog.text
    assert "secret body" not in caplog.text


@pytest.mark.django_db
def test_retry_visibility_uses_bounded_exponential_backoff(
    due_event, weather_provider
):
    due_event.transition_to(ScheduledEvent.Status.PROCESSING)
    due_event.save(update_fields=["status", "processing_started_at"])
    queue = Mock()
    message = message_for(
        QueueEnvelope.deliver_event(due_event.id),
        receive_count=5,
    )

    process_queue_batch(
        queue=queue,
        messages=(message,),
        weather_provider=weather_provider,
        message_sender=None,
        batch_size=10,
        max_receive_count=3,
        retry_base_seconds=30,
        retry_max_seconds=300,
    )

    queue.change_visibility.assert_called_once_with(message, visibility_timeout=300)
