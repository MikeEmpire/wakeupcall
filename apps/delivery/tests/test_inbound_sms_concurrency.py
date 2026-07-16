from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.delivery.models import InboundSmsCommand
from apps.delivery.services import apply_inbound_sms_command
from apps.scheduling.models import ScheduledEvent

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="PostgreSQL row-lock behavior cannot be validated on SQLite.",
    ),
]

MESSAGE_SID = "SM" + "f" * 32
SENDER = "+14155552671"


def create_context():
    user = get_user_model().objects.create_user(username="sms-command-concurrency")
    phone = PhoneNumber.objects.create(
        user=user,
        number=SENDER,
        verified_at=timezone.now(),
    )
    event = ScheduledEvent.objects.create(
        user=user,
        phone_number=phone,
        zip_code="94107",
        scheduled_for=timezone.now() + timedelta(hours=1),
        channel=ScheduledEvent.Channel.VOICE,
        is_demo=True,
    )
    return event


def run_command(barrier, body):
    close_old_connections()
    try:
        barrier.wait()
        result = apply_inbound_sms_command(
            provider_sid=MESSAGE_SID,
            sender=SENDER,
            body=body,
        )
        return result.outcome, result.applied
    finally:
        close_old_connections()


def test_concurrent_conflicting_duplicates_apply_once():
    event = create_context()
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(run_command, barrier, body) for body in ("STOP", "SMS")
        ]
        results = [future.result() for future in futures]

    event.refresh_from_db()
    command = InboundSmsCommand.objects.get(provider_sid=MESSAGE_SID)
    assert InboundSmsCommand.objects.count() == 1
    assert sum(applied for _outcome, applied in results) == 1
    assert {outcome for outcome, _applied in results} == {command.result}
    if command.result == InboundSmsCommand.Result.CANCELLED:
        assert event.status == ScheduledEvent.Status.CANCELLED
        assert event.channel == ScheduledEvent.Channel.VOICE
    else:
        assert command.result == InboundSmsCommand.Result.SWITCHED_TO_SMS
        assert event.status == ScheduledEvent.Status.SCHEDULED
        assert event.channel == ScheduledEvent.Channel.SMS
