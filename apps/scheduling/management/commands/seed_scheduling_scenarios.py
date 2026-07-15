from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.delivery.models import DeliveryAttempt
from apps.scheduling.models import ScheduledEvent

SEED_USERNAME = "wakeupcall-scenarios"
SEED_PHONE_NUMBER = "+15555550199"


@dataclass(frozen=True)
class EventScenario:
    timing: str
    status: str
    channel: str
    is_demo: bool


def build_scenarios():
    scenarios = []
    scenarios.extend(
        EventScenario("due", ScheduledEvent.Status.SCHEDULED, channel, True)
        for channel in ([ScheduledEvent.Channel.SMS, ScheduledEvent.Channel.VOICE] * 3)
    )
    scenarios.extend(
        EventScenario("future", ScheduledEvent.Status.SCHEDULED, channel, True)
        for channel in ([ScheduledEvent.Channel.SMS, ScheduledEvent.Channel.VOICE] * 2)
    )
    scenarios.extend(
        EventScenario("missed", ScheduledEvent.Status.SCHEDULED, channel, True)
        for channel in ([ScheduledEvent.Channel.SMS, ScheduledEvent.Channel.VOICE] * 2)
    )
    scenarios.extend(
        EventScenario("due", ScheduledEvent.Status.SCHEDULED, channel, False)
        for channel in ([ScheduledEvent.Channel.SMS, ScheduledEvent.Channel.VOICE] * 2)
    )
    scenarios.extend(
        EventScenario("past", ScheduledEvent.Status.SUPPRESSED, channel, True)
        for channel in [
            ScheduledEvent.Channel.SMS,
            ScheduledEvent.Channel.VOICE,
            ScheduledEvent.Channel.SMS,
        ]
    )
    scenarios.extend(
        EventScenario("past", ScheduledEvent.Status.SUBMITTED, channel, False)
        for channel in [
            ScheduledEvent.Channel.SMS,
            ScheduledEvent.Channel.VOICE,
            ScheduledEvent.Channel.SMS,
        ]
    )
    scenarios.extend(
        EventScenario("past", ScheduledEvent.Status.FAILED, channel, True)
        for channel in [
            ScheduledEvent.Channel.VOICE,
            ScheduledEvent.Channel.SMS,
            ScheduledEvent.Channel.VOICE,
        ]
    )
    scenarios.extend(
        EventScenario("past", ScheduledEvent.Status.CANCELLED, channel, True)
        for channel in [ScheduledEvent.Channel.SMS, ScheduledEvent.Channel.VOICE]
    )
    scenarios.append(
        EventScenario(
            "stale",
            ScheduledEvent.Status.PROCESSING,
            ScheduledEvent.Channel.SMS,
            True,
        )
    )
    if len(scenarios) != 30:
        raise RuntimeError("The scheduling seed must contain exactly 30 scenarios.")
    return scenarios


class Command(BaseCommand):
    help = "Replace the reserved scenario seed with 30 deterministic events."

    @transaction.atomic
    def handle(self, *args, **options):
        user, _ = get_user_model().objects.get_or_create(username=SEED_USERNAME)
        phone, _ = PhoneNumber.objects.update_or_create(
            number=SEED_PHONE_NUMBER,
            defaults={"user": user, "verified_at": timezone.now()},
        )
        ScheduledEvent.objects.filter(user=user).delete()

        now = timezone.now().replace(second=0, microsecond=0)
        for index, scenario in enumerate(build_scenarios(), start=1):
            scheduled_for = self._scheduled_time(now, scenario.timing, index)
            event = ScheduledEvent.objects.create(
                user=user,
                phone_number=phone,
                zip_code="94107",
                scheduled_for=scheduled_for,
                channel=scenario.channel,
                is_demo=scenario.is_demo,
            )
            self._apply_status(event, scenario.status, now=now, index=index)

        self.stdout.write(
            self.style.SUCCESS("Created 30 scheduling scenarios for the reserved seed user.")
        )

    @staticmethod
    def _scheduled_time(now, timing, index):
        jitter = timedelta(seconds=index)
        if timing == "future":
            return now + timedelta(minutes=30) + jitter
        if timing == "missed":
            return now - timedelta(minutes=30) + jitter
        if timing == "stale":
            return now - timedelta(hours=2) + jitter
        return now - timedelta(minutes=5) + jitter

    @staticmethod
    def _apply_status(event, status, *, now, index):
        if status == ScheduledEvent.Status.SCHEDULED:
            return
        if status == ScheduledEvent.Status.CANCELLED:
            event.transition_to(status, at=now)
            event.save(update_fields=["status", "completed_at", "updated_at"])
            return

        processing_at = (
            now - timedelta(hours=1)
            if status == ScheduledEvent.Status.PROCESSING
            else now
        )
        event.transition_to(ScheduledEvent.Status.PROCESSING, at=processing_at)
        event.save(update_fields=["status", "processing_started_at", "updated_at"])
        attempt = DeliveryAttempt.objects.create(event=event, attempt_number=1)
        if status == ScheduledEvent.Status.PROCESSING:
            return

        attempt.transition_to(status, at=now)
        attempt.rendered_message = "Seeded weather announcement."
        attempt.weather_snapshot = {
            "location": "ZIP 94107",
            "temperature_f": 72.0,
            "condition": "clear skies",
            "observed_at": now.isoformat(),
        }
        if status == ScheduledEvent.Status.SUBMITTED:
            prefix = "SM" if event.channel == ScheduledEvent.Channel.SMS else "CA"
            attempt.provider_sid = f"{prefix}{index:032x}"
        elif status == ScheduledEvent.Status.FAILED:
            attempt.error_code = "SeededFailure"
            attempt.error_message = "Deterministic seeded failure."
        attempt.save(
            update_fields=[
                "status",
                "rendered_message",
                "weather_snapshot",
                "provider_sid",
                "error_code",
                "error_message",
                "completed_at",
            ]
        )
        event.transition_to(status, at=now)
        event.save(update_fields=["status", "completed_at", "updated_at"])
