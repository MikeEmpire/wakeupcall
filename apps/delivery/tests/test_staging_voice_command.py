from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.delivery.gateways import DeliveryResult
from apps.scheduling.models import ScheduledEvent

AUTHORIZED_NUMBER = "+14155552671"


@pytest.fixture
def voice_event(db):
    user = get_user_model().objects.create_user(username="voice-smoke-user")
    phone = PhoneNumber.objects.create(
        user=user,
        number=AUTHORIZED_NUMBER,
        verified_at=timezone.now(),
    )
    return ScheduledEvent.objects.create(
        user=user,
        phone_number=phone,
        zip_code="94107",
        scheduled_for=timezone.now() - timedelta(minutes=1),
        channel=ScheduledEvent.Channel.VOICE,
        is_demo=False,
    )


@pytest.mark.django_db
@override_settings(TWILIO_VOICE_SMOKE_ENABLED=False)
def test_staging_voice_command_is_disabled_by_default(voice_event):
    with pytest.raises(CommandError, match="disabled"):
        call_command("send_staging_voice_event", voice_event.id, confirm_call=True)


@pytest.mark.django_db
@override_settings(
    TWILIO_VOICE_SMOKE_ENABLED=True,
    TWILIO_VOICE_SMOKE_TO_NUMBER=AUTHORIZED_NUMBER,
)
def test_staging_voice_command_requires_confirmation(voice_event):
    with pytest.raises(CommandError, match="--confirm-call"):
        call_command("send_staging_voice_event", voice_event.id)


@pytest.mark.django_db
@override_settings(
    TWILIO_VOICE_SMOKE_ENABLED=True,
    TWILIO_VOICE_SMOKE_TO_NUMBER=AUTHORIZED_NUMBER,
)
def test_staging_voice_command_rejects_demo_before_sender_creation(
    voice_event,
    monkeypatch,
):
    voice_event.is_demo = True
    voice_event.save(update_fields=["is_demo"])
    sender_factory = Mock()
    monkeypatch.setattr(
        "apps.delivery.management.commands.send_staging_voice_event."
        "TwilioVoiceSender.from_settings",
        sender_factory,
    )

    with pytest.raises(CommandError, match="Demo events"):
        call_command("send_staging_voice_event", voice_event.id, confirm_call=True)

    sender_factory.assert_not_called()


@pytest.mark.django_db
@override_settings(
    TWILIO_VOICE_SMOKE_ENABLED=True,
    TWILIO_VOICE_SMOKE_TO_NUMBER=AUTHORIZED_NUMBER,
)
def test_staging_voice_command_submits_authorized_event(
    voice_event,
    monkeypatch,
    capsys,
):
    sender = Mock()
    sender.send.return_value = DeliveryResult(provider_sid="CA" + "0" * 32)
    monkeypatch.setattr(
        "apps.delivery.management.commands.send_staging_voice_event."
        "TwilioVoiceSender.from_settings",
        Mock(return_value=sender),
    )

    call_command("send_staging_voice_event", voice_event.id, confirm_call=True)

    voice_event.refresh_from_db()
    output = capsys.readouterr().out
    assert voice_event.status == ScheduledEvent.Status.SUBMITTED
    sender.send.assert_called_once()
    assert AUTHORIZED_NUMBER not in output
    assert "Wake up" not in output
