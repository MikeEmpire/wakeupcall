from datetime import timedelta
from unittest.mock import ANY, Mock

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.scheduling.admin import ScheduledEventAdmin
from apps.scheduling.models import ScheduledEvent


@pytest.mark.django_db
def test_admin_action_cancels_only_selected_scheduled_events():
    user = get_user_model().objects.create_user(username="admin-action-user")
    phone = PhoneNumber.objects.create(
        user=user,
        number="+14155552671",
        verified_at=timezone.now(),
    )
    scheduled = ScheduledEvent.objects.create(
        user=user,
        phone_number=phone,
        zip_code="94107",
        scheduled_for=timezone.now() + timedelta(hours=1),
        channel=ScheduledEvent.Channel.SMS,
    )
    processing = ScheduledEvent.objects.create(
        user=user,
        phone_number=phone,
        zip_code="94107",
        scheduled_for=timezone.now() + timedelta(hours=2),
        channel=ScheduledEvent.Channel.VOICE,
        status=ScheduledEvent.Status.PROCESSING,
        processing_started_at=timezone.now(),
    )
    model_admin = ScheduledEventAdmin(ScheduledEvent, admin.site)
    model_admin.message_user = Mock()

    model_admin.cancel_selected_scheduled_events(
        Mock(), ScheduledEvent.objects.filter(id__in=[scheduled.id, processing.id])
    )

    scheduled.refresh_from_db()
    processing.refresh_from_db()
    assert scheduled.status == ScheduledEvent.Status.CANCELLED
    assert processing.status == ScheduledEvent.Status.PROCESSING
    model_admin.message_user.assert_called_once_with(
        ANY,
        "Cancelled 1 scheduled event(s).",
    )
