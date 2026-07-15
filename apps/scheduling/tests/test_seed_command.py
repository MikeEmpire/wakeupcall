from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.delivery.models import DeliveryAttempt
from apps.scheduling.management.commands.seed_scheduling_scenarios import (
    SEED_PHONE_NUMBER,
    SEED_USERNAME,
)
from apps.scheduling.models import ScheduledEvent


@pytest.mark.django_db
def test_seed_command_creates_expected_scenario_matrix():
    output = StringIO()

    call_command("seed_scheduling_scenarios", stdout=output)

    events = ScheduledEvent.objects.filter(user__username=SEED_USERNAME)
    assert events.count() == 30
    assert events.filter(status=ScheduledEvent.Status.SCHEDULED).count() == 18
    assert events.filter(status=ScheduledEvent.Status.SUPPRESSED).count() == 3
    assert events.filter(status=ScheduledEvent.Status.SUBMITTED).count() == 3
    assert events.filter(status=ScheduledEvent.Status.FAILED).count() == 3
    assert events.filter(status=ScheduledEvent.Status.CANCELLED).count() == 2
    assert events.filter(status=ScheduledEvent.Status.PROCESSING).count() == 1
    assert events.filter(channel=ScheduledEvent.Channel.SMS).count() == 16
    assert events.filter(channel=ScheduledEvent.Channel.VOICE).count() == 14
    assert events.filter(is_demo=False).count() == 7
    assert events.exclude(phone_number__number=SEED_PHONE_NUMBER).count() == 0
    assert DeliveryAttempt.objects.filter(event__in=events).count() == 10
    assert "Created 30 scheduling scenarios" in output.getvalue()


@pytest.mark.django_db
def test_seed_command_is_repeatable_and_marks_processing_scenario_stale():
    call_command("seed_scheduling_scenarios")
    call_command("seed_scheduling_scenarios")

    user = get_user_model().objects.get(username=SEED_USERNAME)
    events = ScheduledEvent.objects.filter(user=user)
    processing = events.get(status=ScheduledEvent.Status.PROCESSING)
    assert events.count() == 30
    assert processing.processing_started_at < timezone.now() - timedelta(minutes=55)
