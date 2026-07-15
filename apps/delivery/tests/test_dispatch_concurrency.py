from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.delivery.models import DeliveryAttempt
from apps.delivery.gateways import DeliveryResult
from apps.delivery.services import (
    QueueDeliveryAction,
    claim_due_delivery_batch,
    process_queued_delivery,
)
from apps.scheduling.models import ScheduledEvent
from apps.scheduling.services import cancel_scheduled_event
from apps.weather.providers import CurrentWeather

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="PostgreSQL row-lock behavior cannot be validated on SQLite.",
    ),
]


def create_due_event():
    user = get_user_model().objects.create_user(username="concurrency-user")
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


def run_claim(barrier, now):
    close_old_connections()
    try:
        barrier.wait()
        batch = claim_due_delivery_batch(now=now, batch_size=1)
        return batch.selected_count
    finally:
        close_old_connections()


def test_concurrent_dispatchers_claim_due_event_once():
    event = create_due_event()
    barrier = Barrier(2)
    now = timezone.now()

    with ThreadPoolExecutor(max_workers=2) as executor:
        counts = list(executor.map(lambda _: run_claim(barrier, now), range(2)))

    event.refresh_from_db()
    assert sum(counts) == 1
    assert event.status == ScheduledEvent.Status.PROCESSING
    assert DeliveryAttempt.objects.filter(event=event).count() == 1


def run_cancel(barrier, event_id):
    close_old_connections()
    try:
        barrier.wait()
        try:
            cancel_scheduled_event(event_id)
        except ValidationError:
            return "lost"
        return "cancelled"
    finally:
        close_old_connections()


def test_cancellation_and_claim_have_one_legal_winner():
    event = create_due_event()
    barrier = Barrier(2)
    now = timezone.now()

    with ThreadPoolExecutor(max_workers=2) as executor:
        claim_future = executor.submit(run_claim, barrier, now)
        cancel_future = executor.submit(run_cancel, barrier, event.id)
        claimed_count = claim_future.result()
        cancel_result = cancel_future.result()

    event.refresh_from_db()
    attempt_count = DeliveryAttempt.objects.filter(event=event).count()
    outcomes = {
        (ScheduledEvent.Status.CANCELLED, 0, 0, "cancelled"),
        (ScheduledEvent.Status.PROCESSING, 1, 1, "lost"),
    }
    assert (event.status, attempt_count, claimed_count, cancel_result) in outcomes


def run_queued_delivery(barrier, event_id, weather_provider, demo_sender):
    close_old_connections()
    try:
        barrier.wait()
        return process_queued_delivery(
            event_id,
            receive_count=1,
            max_receive_count=3,
            weather_provider=weather_provider,
            demo_sender=demo_sender,
        )
    finally:
        close_old_connections()


def test_duplicate_queue_messages_execute_provider_once():
    event = create_due_event()
    barrier = Barrier(2)
    weather_provider = Mock()
    weather_provider.get_current_weather.return_value = CurrentWeather(
        location="San Francisco",
        temperature_f=62.0,
        condition="clear skies",
        observed_at=timezone.now(),
    )
    demo_sender = Mock()
    demo_sender.send.return_value = DeliveryResult()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                run_queued_delivery,
                barrier,
                event.id,
                weather_provider,
                demo_sender,
            )
            for _ in range(2)
        ]
        actions = [future.result() for future in futures]

    event.refresh_from_db()
    assert QueueDeliveryAction.ACKNOWLEDGE in actions
    assert set(actions) <= {
        QueueDeliveryAction.ACKNOWLEDGE,
        QueueDeliveryAction.RETRY,
    }
    assert event.status == ScheduledEvent.Status.SUPPRESSED
    assert DeliveryAttempt.objects.filter(event=event).count() == 1
    demo_sender.send.assert_called_once()
