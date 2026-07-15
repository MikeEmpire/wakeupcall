from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.delivery.gateways import DemoMessageSender, MessageSender
from apps.delivery.messages import render_weather_announcement
from apps.delivery.models import DeliveryAttempt
from apps.scheduling.models import ScheduledEvent
from apps.weather.providers import WeatherProvider


class EventNotDue(ValidationError):
    pass


class MessageSenderNotConfigured(RuntimeError):
    pass


class DeliveryAttemptNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaimedDelivery:
    event: ScheduledEvent
    attempt: DeliveryAttempt


@dataclass(frozen=True)
class ProviderStatusUpdate:
    attempt: DeliveryAttempt
    applied: bool


def deliver_scheduled_event(
    event_id: int,
    *,
    weather_provider: WeatherProvider,
    message_sender: MessageSender | None = None,
    demo_sender: MessageSender | None = None,
    now=None,
) -> DeliveryAttempt:
    delivery_time = now or timezone.now()
    claimed = _claim_event(event_id, delivery_time)

    if claimed is None:
        latest_attempt = (
            DeliveryAttempt.objects.filter(event_id=event_id)
            .order_by("-attempt_number")
            .first()
        )
        if latest_attempt is None:
            raise ValidationError("A completed event must have a delivery attempt.")
        return latest_attempt

    event = claimed.event
    attempt = claimed.attempt

    try:
        weather = weather_provider.get_current_weather(event.zip_code)
        message = render_weather_announcement(weather)
        sender = _select_sender(
            event,
            message_sender=message_sender,
            demo_sender=demo_sender,
        )
        result = sender.send(
            channel=event.channel,
            to=event.phone_number.number,
            message=message,
        )
    except Exception as exc:
        _record_failure(event.id, attempt.id, exc, timezone.now())
        raise

    terminal_status = (
        DeliveryAttempt.Status.SUPPRESSED
        if event.is_demo
        else DeliveryAttempt.Status.SUBMITTED
    )
    return _record_success(
        event.id,
        attempt.id,
        status=terminal_status,
        message=message,
        weather_snapshot=weather.as_snapshot(),
        provider_sid=result.provider_sid,
        completed_at=timezone.now(),
    )


@transaction.atomic
def record_voice_status_callback(
    *,
    provider_sid: str,
    provider_status: str,
    sequence_number: int,
    received_at=None,
) -> ProviderStatusUpdate:
    if not provider_sid.startswith("CA"):
        raise ValidationError("A Twilio Call SID is required.")

    try:
        attempt = DeliveryAttempt.objects.select_for_update().get(
            provider_sid=provider_sid,
            event__channel=ScheduledEvent.Channel.VOICE,
            status=DeliveryAttempt.Status.SUBMITTED,
        )
    except DeliveryAttempt.DoesNotExist as exc:
        raise DeliveryAttemptNotFound(
            "No submitted voice attempt matches this provider identifier."
        ) from exc

    applied = attempt.apply_provider_status(
        provider_status,
        sequence_number=sequence_number,
        at=received_at,
    )
    if applied:
        attempt.save(
            update_fields=[
                "provider_status",
                "provider_status_sequence",
                "provider_status_updated_at",
            ]
        )
    return ProviderStatusUpdate(attempt=attempt, applied=applied)


@transaction.atomic
def _claim_event(event_id: int, now) -> ClaimedDelivery | None:
    event = (
        ScheduledEvent.objects.select_for_update()
        .select_related("phone_number")
        .get(id=event_id)
    )

    if event.status in {
        ScheduledEvent.Status.SUBMITTED,
        ScheduledEvent.Status.FAILED,
        ScheduledEvent.Status.SUPPRESSED,
    }:
        return None

    if event.status != ScheduledEvent.Status.SCHEDULED:
        raise ValidationError(
            {"status": f"Event in {event.status} state cannot be delivered."}
        )

    if event.scheduled_for > now:
        raise EventNotDue("The event is not due yet.")

    event.transition_to(ScheduledEvent.Status.PROCESSING, at=now)
    event.save(update_fields=["status", "processing_started_at", "updated_at"])

    previous_attempts = event.delivery_attempts.count()
    attempt = DeliveryAttempt.objects.create(
        event=event,
        attempt_number=previous_attempts + 1,
    )
    return ClaimedDelivery(event=event, attempt=attempt)


def _select_sender(
    event: ScheduledEvent,
    *,
    message_sender: MessageSender | None,
    demo_sender: MessageSender | None,
) -> MessageSender:
    if event.is_demo:
        return demo_sender or DemoMessageSender()
    if message_sender is None:
        raise MessageSenderNotConfigured(
            "A message sender is required for non-demo events."
        )
    return message_sender


@transaction.atomic
def _record_success(
    event_id: int,
    attempt_id: int,
    *,
    status: str,
    message: str,
    weather_snapshot: dict,
    provider_sid: str | None,
    completed_at,
) -> DeliveryAttempt:
    event = ScheduledEvent.objects.select_for_update().get(id=event_id)
    attempt = DeliveryAttempt.objects.select_for_update().get(id=attempt_id)

    attempt.transition_to(status, at=completed_at)
    attempt.rendered_message = message
    attempt.weather_snapshot = weather_snapshot
    attempt.provider_sid = provider_sid
    attempt.save(
        update_fields=[
            "status",
            "rendered_message",
            "weather_snapshot",
            "provider_sid",
            "completed_at",
        ]
    )

    event.transition_to(status, at=completed_at)
    event.save(update_fields=["status", "completed_at", "updated_at"])
    return attempt


@transaction.atomic
def _record_failure(event_id: int, attempt_id: int, exc: Exception, failed_at):
    event = ScheduledEvent.objects.select_for_update().get(id=event_id)
    attempt = DeliveryAttempt.objects.select_for_update().get(id=attempt_id)

    attempt.transition_to(DeliveryAttempt.Status.FAILED, at=failed_at)
    attempt.error_code = type(exc).__name__
    attempt.error_message = str(exc)[:1000]
    attempt.save(
        update_fields=[
            "status",
            "error_code",
            "error_message",
            "completed_at",
        ]
    )

    event.transition_to(ScheduledEvent.Status.FAILED, at=failed_at)
    event.save(update_fields=["status", "completed_at", "updated_at"])
