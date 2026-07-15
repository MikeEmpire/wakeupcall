from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.delivery.models import DeliveryAttempt
from apps.delivery.services import apply_voice_menu_action
from apps.scheduling.models import ScheduledEvent

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="PostgreSQL row-lock behavior cannot be validated on SQLite.",
    ),
]

CALL_SID = "CA" + "e" * 32


def create_action_context():
    user = get_user_model().objects.create_user(username="action-concurrency-user")
    phone = PhoneNumber.objects.create(
        user=user,
        number="+14155552671",
        verified_at=timezone.now(),
    )
    source = ScheduledEvent.objects.create(
        user=user,
        phone_number=phone,
        zip_code="94107",
        scheduled_for=timezone.now() - timedelta(minutes=1),
        channel=ScheduledEvent.Channel.VOICE,
        status=ScheduledEvent.Status.SUBMITTED,
        is_demo=False,
    )
    attempt = DeliveryAttempt.objects.create(
        event=source,
        attempt_number=1,
        status=DeliveryAttempt.Status.SUBMITTED,
        provider_sid=CALL_SID,
    )
    target = ScheduledEvent.objects.create(
        user=user,
        phone_number=phone,
        zip_code="94107",
        scheduled_for=timezone.now() + timedelta(hours=1),
        channel=ScheduledEvent.Channel.VOICE,
        is_demo=True,
    )
    return attempt, target


def run_action(barrier, digit):
    close_old_connections()
    try:
        barrier.wait()
        result = apply_voice_menu_action(provider_sid=CALL_SID, digit=digit)
        return result.outcome, result.applied
    finally:
        close_old_connections()


def test_concurrent_duplicate_actions_apply_once():
    attempt, target = create_action_context()
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(run_action, barrier, digit)
            for digit in ("1", "2")
        ]
        results = [future.result() for future in futures]

    attempt.refresh_from_db()
    target.refresh_from_db()
    assert sum(applied for _outcome, applied in results) == 1
    assert {outcome for outcome, _applied in results} == {attempt.voice_action_result}
    if attempt.voice_action_result == DeliveryAttempt.VoiceActionResult.CANCELLED:
        assert target.status == ScheduledEvent.Status.CANCELLED
        assert target.channel == ScheduledEvent.Channel.VOICE
    else:
        assert target.status == ScheduledEvent.Status.SCHEDULED
        assert target.channel == ScheduledEvent.Channel.SMS
