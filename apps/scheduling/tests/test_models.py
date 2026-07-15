from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.scheduling.models import ScheduledEvent


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="event-owner")


@pytest.fixture
def verified_phone(user):
    return PhoneNumber.objects.create(
        user=user,
        number="+14155552671",
        verified_at=timezone.now(),
    )


def build_event(user, verified_phone, **overrides):
    values = {
        "user": user,
        "phone_number": verified_phone,
        "zip_code": "94107",
        "scheduled_for": timezone.now() + timedelta(hours=1),
        "channel": ScheduledEvent.Channel.SMS,
    }
    values.update(overrides)
    return ScheduledEvent(**values)


@pytest.mark.django_db
def test_event_accepts_verified_phone_owned_by_user(user, verified_phone):
    event = build_event(user, verified_phone)

    event.full_clean()
    event.save()

    assert event.status == ScheduledEvent.Status.SCHEDULED
    assert event.is_demo is True


@pytest.mark.django_db
def test_event_rejects_unverified_phone(user):
    phone = PhoneNumber.objects.create(user=user, number="+14155550000")
    event = build_event(user, phone)

    with pytest.raises(ValidationError, match="must be verified"):
        event.full_clean()


@pytest.mark.django_db
def test_event_rejects_phone_owned_by_another_user(user, verified_phone):
    another_user = get_user_model().objects.create_user(username="another-user")
    event = build_event(another_user, verified_phone)

    with pytest.raises(ValidationError, match="must belong"):
        event.full_clean()


@pytest.mark.django_db
def test_event_rejects_past_scheduled_time(user, verified_phone):
    event = build_event(
        user,
        verified_phone,
        scheduled_for=timezone.now() - timedelta(minutes=1),
    )

    with pytest.raises(ValidationError, match="must be in the future"):
        event.full_clean()


@pytest.mark.django_db
def test_event_follows_allowed_status_transitions(user, verified_phone):
    event = build_event(user, verified_phone)
    event.transition_to(ScheduledEvent.Status.PROCESSING)
    event.transition_to(ScheduledEvent.Status.SUPPRESSED)

    assert event.processing_started_at is not None
    assert event.completed_at is not None


@pytest.mark.django_db
def test_event_rejects_invalid_status_transition(user, verified_phone):
    event = build_event(user, verified_phone)

    with pytest.raises(ValidationError, match="Cannot transition"):
        event.transition_to(ScheduledEvent.Status.SUBMITTED)
