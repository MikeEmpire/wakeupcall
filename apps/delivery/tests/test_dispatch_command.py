from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.delivery.gateways import DeliveryResult
from apps.scheduling.models import ScheduledEvent


@pytest.fixture
def due_event(db):
    user = get_user_model().objects.create_user(username="dispatch-command-user")
    phone = PhoneNumber.objects.create(
        user=user,
        number="+14155552671",
        verified_at=timezone.now(),
    )
    return ScheduledEvent.objects.create(
        user=user,
        phone_number=phone,
        zip_code="94107",
        scheduled_for=timezone.now() - timedelta(minutes=1),
        channel=ScheduledEvent.Channel.SMS,
        is_demo=True,
    )


@pytest.mark.django_db
def test_dispatch_command_is_demo_only_by_default(due_event):
    output = StringIO()

    call_command("dispatch_due_events", stdout=output)

    due_event.refresh_from_db()
    assert due_event.status == ScheduledEvent.Status.SUPPRESSED
    assert "selected=1 delivered=1 missed=0 failed=0" in output.getvalue()


@pytest.mark.django_db
@override_settings(DELIVERY_REAL_DISPATCH_ENABLED=False)
def test_dispatch_command_refuses_real_delivery_without_configuration(due_event):
    with pytest.raises(CommandError, match="disabled by configuration"):
        call_command("dispatch_due_events", allow_real_delivery=True)


@pytest.mark.django_db
@override_settings(DELIVERY_REAL_DISPATCH_ENABLED=True)
def test_dispatch_command_real_delivery_requires_both_explicit_gates(due_event):
    due_event.is_demo = False
    due_event.save(update_fields=["is_demo"])
    sender = Mock()
    sender.send.return_value = DeliveryResult(provider_sid="SM123")

    with patch(
        "apps.delivery.management.commands.dispatch_due_events.TwilioMessageSender",
        return_value=sender,
    ):
        call_command("dispatch_due_events", allow_real_delivery=True)

    sender.send.assert_called_once()
    due_event.refresh_from_db()
    assert due_event.status == ScheduledEvent.Status.SUBMITTED


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("batch_size", 0, "Batch size"),
        ("batch_size", 101, "Batch size"),
        ("grace_minutes", -1, "Grace minutes"),
    ],
)
def test_dispatch_command_rejects_invalid_bounds(due_event, option, value, message):
    with pytest.raises(CommandError, match=message):
        call_command("dispatch_due_events", **{option: value})
