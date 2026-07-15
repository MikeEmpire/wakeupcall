from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.delivery.queueing import QueueEnvelope, ReceivedQueueMessage
from apps.scheduling.models import ScheduledEvent


@pytest.fixture
def due_event(db):
    user = get_user_model().objects.create_user(username="queue-command-user")
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


@pytest.mark.django_db
def test_publish_command_sends_due_demo_identifier(due_event):
    queue = Mock()
    output = StringIO()

    with patch(
        "apps.delivery.management.commands.publish_due_events."
        "SqsDeliveryQueue.from_settings",
        return_value=queue,
    ):
        call_command("publish_due_events", stdout=output)

    queue.publish.assert_called_once_with(QueueEnvelope.deliver_event(due_event.id))
    assert "selected=1 published=1" in output.getvalue()


@pytest.mark.django_db
@override_settings(DELIVERY_REAL_WORKER_ENABLED=False)
def test_publish_command_refuses_real_gate_when_disabled(due_event):
    with pytest.raises(CommandError, match="disabled by configuration"):
        call_command("publish_due_events", allow_real_delivery=True)


@pytest.mark.django_db
def test_publish_command_validates_batch_before_queue_construction(due_event):
    with pytest.raises(CommandError, match="Batch size"):
        call_command("publish_due_events", batch_size=0)


@pytest.mark.django_db
@override_settings(
    WEATHER_API_KEY="weather-key",
    DELIVERY_QUEUE_RECEIVE_BATCH_SIZE=10,
    DELIVERY_QUEUE_WAIT_SECONDS=20,
    DELIVERY_QUEUE_VISIBILITY_SECONDS=120,
    DELIVERY_QUEUE_MAX_RECEIVES=3,
    DELIVERY_QUEUE_RETRY_BASE_SECONDS=30,
    DELIVERY_QUEUE_RETRY_MAX_SECONDS=300,
)
def test_worker_once_long_polls_and_processes_message(due_event):
    queue = Mock()
    queue.receive.return_value = (
        ReceivedQueueMessage(
            body=QueueEnvelope.deliver_event(due_event.id).to_json(),
            receipt_handle="receipt-secret",
            receive_count=1,
            message_id="message-1",
        ),
    )
    weather_provider = Mock()
    weather_provider.get_current_weather.return_value = Mock(
        as_snapshot=Mock(return_value={"location": "San Francisco"}),
        temperature_f=62.0,
        condition="clear skies",
    )
    output = StringIO()

    with (
        patch(
            "apps.delivery.management.commands.run_delivery_worker."
            "SqsDeliveryQueue.from_settings",
            return_value=queue,
        ),
        patch(
            "apps.delivery.management.commands.run_delivery_worker."
            "WeatherApiProvider.from_settings",
            return_value=weather_provider,
        ),
    ):
        call_command("run_delivery_worker", once=True, stdout=output)

    queue.receive.assert_called_once_with(
        max_messages=10,
        wait_time_seconds=20,
        visibility_timeout=120,
    )
    queue.delete.assert_called_once()
    assert "received=1 acknowledged=1 retry=0" in output.getvalue()


@pytest.mark.django_db
@override_settings(DELIVERY_REAL_WORKER_ENABLED=False)
def test_worker_refuses_real_delivery_before_adapter_construction(due_event):
    with pytest.raises(CommandError, match="disabled by configuration"):
        call_command("run_delivery_worker", once=True, allow_real_delivery=True)


@pytest.mark.django_db
@override_settings(DELIVERY_QUEUE_WAIT_SECONDS=21)
def test_worker_rejects_unbounded_poll_configuration(due_event):
    with pytest.raises(CommandError, match="DELIVERY_QUEUE_WAIT_SECONDS"):
        call_command("run_delivery_worker", once=True)
