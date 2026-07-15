from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.scheduling.models import ScheduledEvent


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
    if event.status != ScheduledEvent.Status.SCHEDULED:
        raise ValidationError(
            {"status": f"Event in {event.status} state cannot be cancelled."}
        )

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
    if event.status != ScheduledEvent.Status.SCHEDULED:
        raise ValidationError(
            {"status": f"Event in {event.status} state cannot be cancelled."}
        )

    event.transition_to(
        ScheduledEvent.Status.CANCELLED,
        at=cancelled_at or timezone.now(),
    )
    event.save(update_fields=["status", "completed_at", "updated_at"])
    return event
