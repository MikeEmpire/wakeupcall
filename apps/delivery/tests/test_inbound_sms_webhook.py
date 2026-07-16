from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from twilio.request_validator import RequestValidator

from apps.accounts.models import PhoneNumber
from apps.delivery.models import InboundSmsCommand
from apps.scheduling.models import ScheduledEvent
from apps.scheduling.services import ScheduledEventLifecycleConflict

AUTH_TOKEN = "inbound-sms-secret"
CALLBACK_URL = "https://wake.example.test/twilio/sms/inbound/"
TWILIO_NUMBER = "+14155550100"
SENDER = "+14155552671"
MESSAGE_SID = "SM" + "a" * 32


@pytest.fixture(autouse=True)
def inbound_sms_settings(settings):
    settings.TWILIO_AUTH_TOKEN = AUTH_TOKEN
    settings.TWILIO_SMS_INBOUND_CALLBACK_URL = CALLBACK_URL
    settings.TWILIO_SMS_FROM_NUMBER = TWILIO_NUMBER


@pytest.fixture
def verified_sender(db):
    user = get_user_model().objects.create_user(username="inbound-sms-user")
    phone = PhoneNumber.objects.create(
        user=user,
        number=SENDER,
        verified_at=timezone.now(),
    )
    return user, phone


def create_pending_event(
    user,
    phone,
    *,
    scheduled_for=None,
    channel=ScheduledEvent.Channel.VOICE,
):
    return ScheduledEvent.objects.create(
        user=user,
        phone_number=phone,
        zip_code="94107",
        scheduled_for=scheduled_for or timezone.now() + timedelta(hours=1),
        channel=channel,
        is_demo=True,
    )


def post_inbound(
    client,
    *,
    body,
    sender=SENDER,
    message_sid=MESSAGE_SID,
    signed=True,
    extra=None,
):
    params = {
        "MessageSid": message_sid,
        "From": sender,
        "To": TWILIO_NUMBER,
        "Body": body,
    }
    params.update(extra or {})
    signature = RequestValidator(AUTH_TOKEN).compute_signature(CALLBACK_URL, params)
    return client.post(
        reverse("twilio-inbound-sms"),
        params,
        HTTP_X_TWILIO_SIGNATURE=signature if signed else "invalid",
    )


pytestmark = pytest.mark.django_db


def test_stop_cancels_only_owners_earliest_pending_event(client, verified_sender):
    user, phone = verified_sender
    shared_time = timezone.now() + timedelta(hours=2)
    first = create_pending_event(user, phone, scheduled_for=shared_time)
    second = create_pending_event(user, phone, scheduled_for=shared_time)
    other_user = get_user_model().objects.create_user(username="inbound-other")
    other_phone = PhoneNumber.objects.create(
        user=other_user,
        number="+14155559999",
        verified_at=timezone.now(),
    )
    other = create_pending_event(
        other_user,
        other_phone,
        scheduled_for=timezone.now() + timedelta(minutes=30),
    )

    response = post_inbound(client, body="  stop  ")

    first.refresh_from_db()
    second.refresh_from_db()
    other.refresh_from_db()
    command = InboundSmsCommand.objects.get()
    assert response.status_code == 200
    assert "Next wake-up cancelled." in response.content.decode()
    assert first.status == ScheduledEvent.Status.CANCELLED
    assert second.status == ScheduledEvent.Status.SCHEDULED
    assert other.status == ScheduledEvent.Status.SCHEDULED
    assert command.command == InboundSmsCommand.Command.STOP
    assert command.result == InboundSmsCommand.Result.CANCELLED
    assert command.target_event_id == first.id


def test_sms_switches_earliest_pending_event_to_sms(client, verified_sender):
    user, phone = verified_sender
    target = create_pending_event(user, phone)

    response = post_inbound(client, body="sMs")

    target.refresh_from_db()
    assert response.status_code == 200
    assert "set to SMS" in response.content.decode()
    assert target.channel == ScheduledEvent.Channel.SMS
    assert target.status == ScheduledEvent.Status.SCHEDULED
    assert InboundSmsCommand.objects.get().result == (
        InboundSmsCommand.Result.SWITCHED_TO_SMS
    )


def test_time_reschedules_with_phase_11_time_rules(client, verified_sender):
    user, phone = verified_sender
    target = create_pending_event(user, phone)
    requested_time = (timezone.now() + timedelta(days=2)).replace(microsecond=0)

    response = post_inbound(client, body=f"TIME {requested_time.isoformat()}")

    target.refresh_from_db()
    assert response.status_code == 200
    assert "time updated" in response.content.decode()
    assert target.scheduled_for == requested_time
    assert target.status == ScheduledEvent.Status.SCHEDULED
    assert InboundSmsCommand.objects.get().result == (
        InboundSmsCommand.Result.RESCHEDULED
    )


def test_invalid_signature_is_rejected_before_processing(client, verified_sender):
    user, phone = verified_sender
    target = create_pending_event(user, phone)

    response = post_inbound(client, body="STOP", signed=False)

    target.refresh_from_db()
    assert response.status_code == 403
    assert response.content == b""
    assert target.status == ScheduledEvent.Status.SCHEDULED
    assert not InboundSmsCommand.objects.exists()


def test_wrong_twilio_recipient_is_rejected_safely(client, verified_sender):
    user, phone = verified_sender
    target = create_pending_event(user, phone)
    params = {
        "MessageSid": MESSAGE_SID,
        "From": SENDER,
        "To": "+14155550101",
        "Body": "STOP",
    }
    signature = RequestValidator(AUTH_TOKEN).compute_signature(CALLBACK_URL, params)

    response = client.post(
        reverse("twilio-inbound-sms"),
        params,
        HTTP_X_TWILIO_SIGNATURE=signature,
    )

    target.refresh_from_db()
    assert response.status_code == 200
    assert "Request could not be processed." in response.content.decode()
    assert target.status == ScheduledEvent.Status.SCHEDULED
    assert not InboundSmsCommand.objects.exists()


@pytest.mark.parametrize("verified", [False, True])
def test_unknown_and_unverified_senders_receive_same_safe_result(
    client,
    db,
    verified,
):
    if not verified:
        user = get_user_model().objects.create_user(username="unverified-sender")
        PhoneNumber.objects.create(user=user, number=SENDER)
    sender = SENDER if not verified else "+14155558888"

    response = post_inbound(client, body="STOP", sender=sender)

    command = InboundSmsCommand.objects.get()
    assert response.status_code == 200
    assert "Request could not be processed." in response.content.decode()
    assert command.result == InboundSmsCommand.Result.UNKNOWN_SENDER
    assert command.target_event_id is None


@pytest.mark.parametrize("body", ["", "CANCEL", "SMS NOW", "TIME", "HELP"])
def test_invalid_commands_do_not_change_event(client, verified_sender, body):
    user, phone = verified_sender
    target = create_pending_event(user, phone)

    response = post_inbound(client, body=body)

    target.refresh_from_db()
    assert response.status_code == 200
    assert "Use STOP, SMS, or TIME" in response.content.decode()
    assert target.status == ScheduledEvent.Status.SCHEDULED
    assert target.channel == ScheduledEvent.Channel.VOICE
    assert InboundSmsCommand.objects.get().result == (
        InboundSmsCommand.Result.INVALID_COMMAND
    )


@pytest.mark.parametrize(
    "value",
    [
        "not-a-time",
        "2030-01-01T12:00:00",
        "2000-01-01T00:00:00Z",
        "2030-01-01 12:00:00Z",
    ],
)
def test_invalid_time_values_do_not_change_event(client, verified_sender, value):
    user, phone = verified_sender
    original_time = timezone.now() + timedelta(hours=1)
    target = create_pending_event(user, phone, scheduled_for=original_time)

    response = post_inbound(client, body=f"TIME {value}")

    target.refresh_from_db()
    assert response.status_code == 200
    assert "future ISO 8601 time" in response.content.decode()
    assert target.scheduled_for == original_time
    assert InboundSmsCommand.objects.get().result == (
        InboundSmsCommand.Result.INVALID_TIME
    )


def test_no_pending_event_is_idempotently_recorded(client, verified_sender):
    response = post_inbound(client, body="SMS")
    duplicate = post_inbound(client, body="STOP")

    assert response.status_code == 200
    assert duplicate.status_code == 200
    assert "No pending wake-up." in response.content.decode()
    assert "No pending wake-up." in duplicate.content.decode()
    assert InboundSmsCommand.objects.count() == 1
    assert InboundSmsCommand.objects.get().result == (
        InboundSmsCommand.Result.NO_PENDING_EVENT
    )


def test_sequential_conflicting_duplicate_returns_first_result(
    client,
    verified_sender,
):
    user, phone = verified_sender
    first_target = create_pending_event(user, phone)

    first = post_inbound(client, body="STOP")
    second_target = create_pending_event(user, phone)
    duplicate = post_inbound(client, body="SMS")

    first_target.refresh_from_db()
    second_target.refresh_from_db()
    command = InboundSmsCommand.objects.get()
    assert first.status_code == 200
    assert "cancelled" in duplicate.content.decode()
    assert first_target.status == ScheduledEvent.Status.CANCELLED
    assert second_target.status == ScheduledEvent.Status.SCHEDULED
    assert second_target.channel == ScheduledEvent.Channel.VOICE
    assert command.command == InboundSmsCommand.Command.STOP
    assert command.target_event_id == first_target.id


def test_lifecycle_conflict_returns_safe_result(
    client,
    verified_sender,
    monkeypatch,
):
    user, phone = verified_sender
    target = create_pending_event(user, phone)

    def conflict(*args, **kwargs):
        raise ScheduledEventLifecycleConflict(
            {"status": "Event cannot be cancelled."}
        )

    monkeypatch.setattr("apps.delivery.services.cancel_user_scheduled_event", conflict)
    response = post_inbound(client, body="STOP")

    target.refresh_from_db()
    assert response.status_code == 200
    assert "can no longer be changed" in response.content.decode()
    assert target.status == ScheduledEvent.Status.SCHEDULED
    assert InboundSmsCommand.objects.get().result == (
        InboundSmsCommand.Result.LIFECYCLE_CONFLICT
    )


def test_advanced_opt_out_stop_returns_empty_twiml_after_cancelling(
    client,
    verified_sender,
):
    user, phone = verified_sender
    target = create_pending_event(user, phone)

    response = post_inbound(client, body="STOP", extra={"OptOutType": "STOP"})

    target.refresh_from_db()
    content = response.content.decode()
    assert response.status_code == 200
    assert "<Response" in content
    assert "<Message" not in content
    assert target.status == ScheduledEvent.Status.CANCELLED


def test_responses_and_logs_do_not_expose_callback_data(
    client,
    verified_sender,
    caplog,
):
    user, phone = verified_sender
    target = create_pending_event(user, phone)
    body = "TIME private-invalid-value"

    response = post_inbound(client, body=body)

    content = response.content.decode()
    logs = caplog.text
    for sensitive_value in (SENDER, body, MESSAGE_SID, user.username):
        assert sensitive_value not in content
        assert sensitive_value not in logs
    assert f"event {target.id}" not in content.lower()


def test_inbound_sms_endpoint_is_post_only(client):
    assert client.get(reverse("twilio-inbound-sms")).status_code == 405
