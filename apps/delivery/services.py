import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.delivery.gateways import DemoMessageSender, MessageSender
from apps.delivery.exceptions import MissedDeliveryWindow
from apps.delivery.messages import render_weather_announcement
from apps.delivery.models import DeliveryAttempt
from apps.delivery.models import InboundSmsCommand
from apps.accounts.models import PhoneNumber
from apps.scheduling.models import ScheduledEvent
from apps.scheduling.services import (
    ScheduledEventLifecycleConflict,
    cancel_user_scheduled_event,
    change_user_scheduled_event_channel,
    reschedule_user_scheduled_event,
)
from apps.weather.providers import WeatherProvider

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 25
MAX_BATCH_SIZE = 100
DEFAULT_GRACE_PERIOD = timedelta(minutes=15)


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


@dataclass(frozen=True)
class VoiceMenuActionResult:
    attempt: DeliveryAttempt
    outcome: str
    target_event_id: int | None
    applied: bool


@dataclass(frozen=True)
class InboundSmsCommandResult:
    command: InboundSmsCommand
    outcome: str
    target_event_id: int | None
    applied: bool


@dataclass(frozen=True)
class ClaimedDeliveryBatch:
    deliveries: tuple[ClaimedDelivery, ...]
    missed_attempts: tuple[DeliveryAttempt, ...]
    selected_count: int


@dataclass(frozen=True)
class DispatchResult:
    selected_count: int
    delivered_count: int
    missed_count: int
    failed_count: int


class QueueDeliveryAction(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    RETRY = "retry"


@dataclass(frozen=True)
class QueuedDeliveryClaim:
    action: QueueDeliveryAction | None
    delivery: ClaimedDelivery | None = None


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

    return _execute_claimed_delivery(
        claimed,
        weather_provider=weather_provider,
        message_sender=message_sender,
        demo_sender=demo_sender,
    )


def _execute_claimed_delivery(
    claimed: ClaimedDelivery,
    *,
    weather_provider: WeatherProvider,
    message_sender: MessageSender | None = None,
    demo_sender: MessageSender | None = None,
) -> DeliveryAttempt:
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


def dispatch_due_events(
    *,
    weather_provider: WeatherProvider,
    message_sender: MessageSender | None = None,
    demo_sender: MessageSender | None = None,
    now=None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    grace_period: timedelta = DEFAULT_GRACE_PERIOD,
    include_real: bool = False,
) -> DispatchResult:
    dispatch_time = now or timezone.now()
    batch = claim_due_delivery_batch(
        now=dispatch_time,
        batch_size=batch_size,
        grace_period=grace_period,
        include_real=include_real,
    )
    delivered_count = 0
    failed_count = 0

    for claimed in batch.deliveries:
        try:
            _execute_claimed_delivery(
                claimed,
                weather_provider=weather_provider,
                message_sender=message_sender,
                demo_sender=demo_sender,
            )
        except Exception as exc:
            failed_count += 1
            logger.warning(
                "Dispatched event failed: event_id=%s error_type=%s",
                claimed.event.id,
                type(exc).__name__,
            )
        else:
            delivered_count += 1

    return DispatchResult(
        selected_count=batch.selected_count,
        delivered_count=delivered_count,
        missed_count=len(batch.missed_attempts),
        failed_count=failed_count,
    )


def process_queued_delivery(
    event_id: int,
    *,
    receive_count: int,
    max_receive_count: int,
    weather_provider: WeatherProvider,
    message_sender: MessageSender | None = None,
    demo_sender: MessageSender | None = None,
    now=None,
    grace_period: timedelta = DEFAULT_GRACE_PERIOD,
    allow_real: bool = False,
) -> QueueDeliveryAction:
    if receive_count < 1 or max_receive_count < 1:
        raise ValueError("Queue receive counts must be positive.")

    delivery_time = now or timezone.now()
    claim = _claim_queued_event(
        event_id,
        now=delivery_time,
        grace_period=grace_period,
        allow_real=allow_real,
    )
    if claim.action is not None:
        return claim.action

    claimed = claim.delivery
    if claimed is None:
        raise RuntimeError("A queued delivery claim did not contain an event.")
    event = claimed.event
    attempt = claimed.attempt

    try:
        weather = weather_provider.get_current_weather(event.zip_code)
        message = render_weather_announcement(weather)
    except Exception as exc:
        if getattr(exc, "retryable", False):
            if receive_count < max_receive_count:
                _record_retryable_failure(event.id, attempt.id, exc, timezone.now())
            else:
                _record_failure(
                    event.id,
                    attempt.id,
                    exc,
                    timezone.now(),
                    error_code=f"RetryExhausted:{type(exc).__name__}"[:64],
                )
            return QueueDeliveryAction.RETRY
        _record_failure(event.id, attempt.id, exc, timezone.now())
        return QueueDeliveryAction.ACKNOWLEDGE

    try:
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
        # Once the sender boundary is entered, a timeout or transport error may
        # hide a provider-accepted request. Automatic replay is therefore unsafe.
        _record_failure(event.id, attempt.id, exc, timezone.now())
        return QueueDeliveryAction.ACKNOWLEDGE

    terminal_status = (
        DeliveryAttempt.Status.SUPPRESSED
        if event.is_demo
        else DeliveryAttempt.Status.SUBMITTED
    )
    _record_success(
        event.id,
        attempt.id,
        status=terminal_status,
        message=message,
        weather_snapshot=weather.as_snapshot(),
        provider_sid=result.provider_sid,
        completed_at=timezone.now(),
    )
    return QueueDeliveryAction.ACKNOWLEDGE


@transaction.atomic
def claim_due_delivery_batch(
    *,
    now,
    batch_size: int = DEFAULT_BATCH_SIZE,
    grace_period: timedelta = DEFAULT_GRACE_PERIOD,
    include_real: bool = False,
) -> ClaimedDeliveryBatch:
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"Batch size must be between 1 and {MAX_BATCH_SIZE}.")
    if grace_period.total_seconds() < 0:
        raise ValueError("Grace period cannot be negative.")

    candidates = ScheduledEvent.objects.filter(
        status=ScheduledEvent.Status.SCHEDULED,
        scheduled_for__lte=now,
    )
    if not include_real:
        candidates = candidates.filter(is_demo=True)
    events = list(
        candidates.select_for_update(skip_locked=True, of=("self",))
        .select_related("phone_number")
        .order_by("scheduled_for", "id")[:batch_size]
    )

    deliveries = []
    missed_attempts = []
    cutoff = now - grace_period
    for event in events:
        claimed = _claim_locked_event(event, now)
        if event.scheduled_for < cutoff:
            missed_attempts.append(
                _record_missed_locked(event, claimed.attempt, failed_at=now)
            )
        else:
            deliveries.append(claimed)

    return ClaimedDeliveryBatch(
        deliveries=tuple(deliveries),
        missed_attempts=tuple(missed_attempts),
        selected_count=len(events),
    )


@transaction.atomic
def _claim_queued_event(
    event_id: int,
    *,
    now,
    grace_period: timedelta,
    allow_real: bool,
) -> QueuedDeliveryClaim:
    if grace_period.total_seconds() < 0:
        raise ValueError("Grace period cannot be negative.")
    try:
        event = (
            ScheduledEvent.objects.select_for_update()
            .select_related("phone_number")
            .get(id=event_id)
        )
    except ScheduledEvent.DoesNotExist:
        return QueuedDeliveryClaim(action=QueueDeliveryAction.ACKNOWLEDGE)

    if event.status == ScheduledEvent.Status.FAILED:
        latest_attempt = event.delivery_attempts.order_by("-attempt_number").first()
        if latest_attempt and latest_attempt.error_code.startswith("RetryExhausted:"):
            return QueuedDeliveryClaim(action=QueueDeliveryAction.RETRY)
        return QueuedDeliveryClaim(action=QueueDeliveryAction.ACKNOWLEDGE)
    if event.status in {
        ScheduledEvent.Status.SUBMITTED,
        ScheduledEvent.Status.SUPPRESSED,
        ScheduledEvent.Status.CANCELLED,
    }:
        return QueuedDeliveryClaim(action=QueueDeliveryAction.ACKNOWLEDGE)
    if event.status == ScheduledEvent.Status.PROCESSING:
        latest_attempt = event.delivery_attempts.order_by("-attempt_number").first()
        if (
            latest_attempt
            and latest_attempt.status == DeliveryAttempt.Status.FAILED
            and latest_attempt.error_code.startswith("QueueRetryable:")
        ):
            return QueuedDeliveryClaim(
                action=None,
                delivery=ClaimedDelivery(
                    event=event,
                    attempt=_create_attempt_locked(event),
                ),
            )
        return QueuedDeliveryClaim(action=QueueDeliveryAction.RETRY)
    if event.scheduled_for > now or (not event.is_demo and not allow_real):
        return QueuedDeliveryClaim(action=QueueDeliveryAction.ACKNOWLEDGE)

    claimed = _claim_locked_event(event, now)
    if event.scheduled_for < now - grace_period:
        _record_missed_locked(event, claimed.attempt, failed_at=now)
        return QueuedDeliveryClaim(action=QueueDeliveryAction.ACKNOWLEDGE)
    return QueuedDeliveryClaim(action=None, delivery=claimed)


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
def apply_voice_menu_action(
    *,
    provider_sid: str,
    digit: str,
    completed_at=None,
) -> VoiceMenuActionResult:
    if digit not in {"1", "2"}:
        raise ValidationError({"digit": "Select a supported voice menu option."})

    try:
        attempt = (
            DeliveryAttempt.objects.select_for_update()
            .select_related("event")
            .get(
                provider_sid=provider_sid,
                event__channel=ScheduledEvent.Channel.VOICE,
                status=DeliveryAttempt.Status.SUBMITTED,
            )
        )
    except DeliveryAttempt.DoesNotExist as exc:
        raise DeliveryAttemptNotFound(
            "No submitted voice attempt matches this provider identifier."
        ) from exc

    if attempt.voice_action_result:
        return VoiceMenuActionResult(
            attempt=attempt,
            outcome=attempt.voice_action_result,
            target_event_id=attempt.voice_action_target_event_id,
            applied=False,
        )

    target = (
        ScheduledEvent.objects.select_for_update()
        .filter(
            user_id=attempt.event.user_id,
            status=ScheduledEvent.Status.SCHEDULED,
        )
        .order_by("scheduled_for", "id")
        .first()
    )

    if target is None:
        outcome = DeliveryAttempt.VoiceActionResult.NO_PENDING_EVENT
        target_event_id = None
    elif digit == "1":
        cancel_user_scheduled_event(
            target.id,
            user=attempt.event.user,
            cancelled_at=completed_at or timezone.now(),
        )
        outcome = DeliveryAttempt.VoiceActionResult.CANCELLED
        target_event_id = target.id
    else:
        change_user_scheduled_event_channel(
            target.id,
            user=attempt.event.user,
            channel=ScheduledEvent.Channel.SMS,
        )
        outcome = DeliveryAttempt.VoiceActionResult.SWITCHED_TO_SMS
        target_event_id = target.id

    attempt.voice_action_digit = digit
    attempt.voice_action_result = outcome
    attempt.voice_action_target_event_id = target_event_id
    attempt.voice_action_completed_at = completed_at or timezone.now()
    attempt.save(
        update_fields=[
            "voice_action_digit",
            "voice_action_result",
            "voice_action_target_event_id",
            "voice_action_completed_at",
        ]
    )
    return VoiceMenuActionResult(
        attempt=attempt,
        outcome=outcome,
        target_event_id=target_event_id,
        applied=True,
    )


def _parse_inbound_sms_command(body: str):
    normalized = body.strip()
    keyword, separator, argument = normalized.partition(" ")
    keyword = keyword.upper()
    if keyword == "STOP" and not separator:
        return InboundSmsCommand.Command.STOP, None
    if keyword == "SMS" and not separator:
        return InboundSmsCommand.Command.SMS, None
    if keyword != "TIME" or not separator or not argument.strip():
        return InboundSmsCommand.Command.INVALID, None

    raw_time = argument.strip()
    if any(character.isspace() for character in raw_time):
        return InboundSmsCommand.Command.TIME, None
    try:
        scheduled_for = datetime.fromisoformat(raw_time)
    except ValueError:
        scheduled_for = None
    return InboundSmsCommand.Command.TIME, scheduled_for


@transaction.atomic
def apply_inbound_sms_command(
    *,
    provider_sid: str,
    sender: str,
    body: str,
    completed_at=None,
) -> InboundSmsCommandResult:
    command_record, created = (
        InboundSmsCommand.objects.select_for_update().get_or_create(
            provider_sid=provider_sid,
            defaults={
                "command": InboundSmsCommand.Command.INVALID,
                "result": InboundSmsCommand.Result.INVALID_COMMAND,
                "completed_at": completed_at or timezone.now(),
            },
        )
    )
    if not created:
        return InboundSmsCommandResult(
            command=command_record,
            outcome=command_record.result,
            target_event_id=command_record.target_event_id,
            applied=False,
        )

    command, scheduled_for = _parse_inbound_sms_command(body)
    phone = (
        PhoneNumber.objects.select_related("user")
        .filter(number=sender, verified_at__isnull=False)
        .first()
    )

    target = None
    if phone is None:
        outcome = InboundSmsCommand.Result.UNKNOWN_SENDER
    elif command == InboundSmsCommand.Command.INVALID:
        outcome = InboundSmsCommand.Result.INVALID_COMMAND
    elif command == InboundSmsCommand.Command.TIME and scheduled_for is None:
        outcome = InboundSmsCommand.Result.INVALID_TIME
    else:
        target = (
            ScheduledEvent.objects.select_for_update()
            .filter(
                user_id=phone.user_id,
                status=ScheduledEvent.Status.SCHEDULED,
            )
            .order_by("scheduled_for", "id")
            .first()
        )
        if target is None:
            outcome = InboundSmsCommand.Result.NO_PENDING_EVENT
        else:
            try:
                if command == InboundSmsCommand.Command.STOP:
                    cancel_user_scheduled_event(
                        target.id,
                        user=phone.user,
                        cancelled_at=completed_at or timezone.now(),
                    )
                    outcome = InboundSmsCommand.Result.CANCELLED
                elif command == InboundSmsCommand.Command.SMS:
                    change_user_scheduled_event_channel(
                        target.id,
                        user=phone.user,
                        channel=ScheduledEvent.Channel.SMS,
                    )
                    outcome = InboundSmsCommand.Result.SWITCHED_TO_SMS
                else:
                    reschedule_user_scheduled_event(
                        target.id,
                        user=phone.user,
                        scheduled_for=scheduled_for,
                    )
                    outcome = InboundSmsCommand.Result.RESCHEDULED
            except ScheduledEventLifecycleConflict:
                outcome = InboundSmsCommand.Result.LIFECYCLE_CONFLICT
            except ValidationError:
                outcome = InboundSmsCommand.Result.INVALID_TIME

    command_record.command = command
    command_record.result = outcome
    command_record.target_event_id = target.id if target is not None else None
    command_record.completed_at = completed_at or timezone.now()
    command_record.save(
        update_fields=["command", "result", "target_event_id", "completed_at"]
    )
    return InboundSmsCommandResult(
        command=command_record,
        outcome=outcome,
        target_event_id=command_record.target_event_id,
        applied=True,
    )


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

    return _claim_locked_event(event, now)


def _claim_locked_event(event: ScheduledEvent, now) -> ClaimedDelivery:
    event.transition_to(ScheduledEvent.Status.PROCESSING, at=now)
    event.save(update_fields=["status", "processing_started_at", "updated_at"])

    return ClaimedDelivery(event=event, attempt=_create_attempt_locked(event))


def _create_attempt_locked(event: ScheduledEvent) -> DeliveryAttempt:

    previous_attempts = event.delivery_attempts.count()
    return DeliveryAttempt.objects.create(
        event=event,
        attempt_number=previous_attempts + 1,
    )


def _record_missed_locked(
    event: ScheduledEvent,
    attempt: DeliveryAttempt,
    *,
    failed_at,
) -> DeliveryAttempt:
    exc = MissedDeliveryWindow(
        "The event was outside the configured delivery grace window."
    )
    attempt.transition_to(DeliveryAttempt.Status.FAILED, at=failed_at)
    attempt.error_code = type(exc).__name__
    attempt.error_message = str(exc)
    attempt.save(
        update_fields=["status", "error_code", "error_message", "completed_at"]
    )

    event.transition_to(ScheduledEvent.Status.FAILED, at=failed_at)
    event.save(update_fields=["status", "completed_at", "updated_at"])
    return attempt


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
def _record_retryable_failure(
    event_id: int,
    attempt_id: int,
    exc: Exception,
    failed_at,
):
    ScheduledEvent.objects.select_for_update().get(id=event_id)
    attempt = DeliveryAttempt.objects.select_for_update().get(id=attempt_id)

    attempt.transition_to(DeliveryAttempt.Status.FAILED, at=failed_at)
    attempt.error_code = f"QueueRetryable:{type(exc).__name__}"[:64]
    attempt.error_message = str(exc)[:1000]
    attempt.save(
        update_fields=["status", "error_code", "error_message", "completed_at"]
    )


@transaction.atomic
def _record_failure(
    event_id: int,
    attempt_id: int,
    exc: Exception,
    failed_at,
    *,
    error_code: str | None = None,
):
    event = ScheduledEvent.objects.select_for_update().get(id=event_id)
    attempt = DeliveryAttempt.objects.select_for_update().get(id=attempt_id)

    attempt.transition_to(DeliveryAttempt.Status.FAILED, at=failed_at)
    attempt.error_code = error_code or type(exc).__name__
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
