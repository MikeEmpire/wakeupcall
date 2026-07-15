from datetime import UTC, datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.scheduling.models import ScheduledEvent


class ScheduledEventLifecycleConflict(ValidationError):
    pass


def _require_scheduled(event: ScheduledEvent, *, action: str) -> None:
    if event.status != ScheduledEvent.Status.SCHEDULED:
        raise ScheduledEventLifecycleConflict(
            {"status": f"Event in {event.status} state cannot be {action}."}
        )


@transaction.atomic
def create_scheduled_event(
    *,
    user,
    phone_number_id: int,
    zip_code: str,
    scheduled_for,
    channel: str,
) -> ScheduledEvent:
    try:
        phone_number = PhoneNumber.objects.select_for_update().get(
            id=phone_number_id,
            user=user,
            verified_at__isnull=False,
        )
    except PhoneNumber.DoesNotExist as exc:
        raise ValidationError(
            {"phone_number_id": "Select a verified phone number you own."}
        ) from exc

    event = ScheduledEvent(
        user=user,
        phone_number=phone_number,
        zip_code=zip_code,
        scheduled_for=scheduled_for,
        channel=channel,
        is_demo=True,
    )
    event.full_clean()
    event.save()
    return event


@transaction.atomic
def cancel_scheduled_event(event_id: int, *, cancelled_at=None) -> ScheduledEvent:
    event = ScheduledEvent.objects.select_for_update().get(id=event_id)
    _require_scheduled(event, action="cancelled")

    event.transition_to(
        ScheduledEvent.Status.CANCELLED,
        at=cancelled_at or timezone.now(),
    )
    event.save(update_fields=["status", "completed_at", "updated_at"])
    return event


@transaction.atomic
def cancel_user_scheduled_event(
    event_id: int,
    *,
    user,
    cancelled_at=None,
) -> ScheduledEvent:
    event = ScheduledEvent.objects.select_for_update().get(id=event_id, user=user)
    _require_scheduled(event, action="cancelled")

    event.transition_to(
        ScheduledEvent.Status.CANCELLED,
        at=cancelled_at or timezone.now(),
    )
    event.save(update_fields=["status", "completed_at", "updated_at"])
    return event


@transaction.atomic
def reschedule_user_scheduled_event(
    event_id: int,
    *,
    user,
    scheduled_for: datetime,
    now=None,
) -> ScheduledEvent:
    event = ScheduledEvent.objects.select_for_update().get(id=event_id, user=user)
    _require_scheduled(event, action="rescheduled")

    if not isinstance(scheduled_for, datetime) or timezone.is_naive(scheduled_for):
        raise ValidationError(
            {"scheduled_for": "Include an explicit UTC offset, such as Z or +00:00."}
        )
    if scheduled_for <= (now or timezone.now()):
        raise ValidationError(
            {"scheduled_for": "The scheduled time must be in the future."}
        )

    event.scheduled_for = scheduled_for.astimezone(UTC)
    event.save(update_fields=["scheduled_for", "updated_at"])
    return event


@transaction.atomic
def change_user_scheduled_event_channel(
    event_id: int,
    *,
    user,
    channel: str,
) -> ScheduledEvent:
    event = ScheduledEvent.objects.select_for_update().get(id=event_id, user=user)
    _require_scheduled(event, action="changed")

    valid_channels = {choice for choice, _label in ScheduledEvent.Channel.choices}
    if channel not in valid_channels:
        raise ValidationError({"channel": "Select a valid choice."})

    event.channel = channel
    event.save(update_fields=["channel", "updated_at"])
    return event
