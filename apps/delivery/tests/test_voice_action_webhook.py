from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from twilio.request_validator import RequestValidator

from apps.accounts.models import PhoneNumber
from apps.delivery.models import DeliveryAttempt
from apps.scheduling.models import ScheduledEvent

AUTH_TOKEN = "action-webhook-secret"
ACTION_URL = "https://wake.example.test/twilio/voice/action/"
CALL_SID = "CA" + "c" * 32


@pytest.fixture
def voice_action_context(db):
    user = get_user_model().objects.create_user(username="voice-action-user")
    phone = PhoneNumber.objects.create(
        user=user,
        number="+14155552671",
        verified_at=timezone.now(),
    )
    source_event = ScheduledEvent.objects.create(
        user=user,
        phone_number=phone,
        zip_code="94107",
        scheduled_for=timezone.now() - timedelta(minutes=1),
        channel=ScheduledEvent.Channel.VOICE,
        is_demo=False,
        status=ScheduledEvent.Status.SUBMITTED,
        completed_at=timezone.now(),
    )
    attempt = DeliveryAttempt.objects.create(
        event=source_event,
        attempt_number=1,
        status=DeliveryAttempt.Status.SUBMITTED,
        provider_sid=CALL_SID,
        completed_at=timezone.now(),
    )
    return user, phone, attempt


def create_pending_event(user, phone, *, hours=1, channel=ScheduledEvent.Channel.VOICE):
    return ScheduledEvent.objects.create(
        user=user,
        phone_number=phone,
        zip_code="94107",
        scheduled_for=timezone.now() + timedelta(hours=hours),
        channel=channel,
        is_demo=True,
    )


def signature(params):
    return RequestValidator(AUTH_TOKEN).compute_signature(ACTION_URL, params)


def post_action(client, *, call_sid=CALL_SID, digit="1", signed=True):
    params = {"CallSid": call_sid, "Digits": digit}
    return client.post(
        reverse("twilio-voice-action"),
        params,
        HTTP_X_TWILIO_SIGNATURE=signature(params) if signed else "invalid",
    )


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def voice_action_settings(settings):
    settings.TWILIO_AUTH_TOKEN = AUTH_TOKEN
    settings.TWILIO_VOICE_ACTION_CALLBACK_URL = ACTION_URL


def test_digit_one_cancels_only_owners_earliest_pending_event(
    client,
    voice_action_context,
):
    user, phone, attempt = voice_action_context
    later = create_pending_event(user, phone, hours=2)
    earlier = create_pending_event(user, phone, hours=1)
    other_user = get_user_model().objects.create_user(username="other-action-user")
    other_phone = PhoneNumber.objects.create(
        user=other_user,
        number="+14155559999",
        verified_at=timezone.now(),
    )
    other = create_pending_event(other_user, other_phone, hours=0.5)

    response = post_action(client, digit="1")

    attempt.refresh_from_db()
    earlier.refresh_from_db()
    later.refresh_from_db()
    other.refresh_from_db()
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/xml")
    assert "has been cancelled" in response.content.decode()
    assert earlier.status == ScheduledEvent.Status.CANCELLED
    assert later.status == ScheduledEvent.Status.SCHEDULED
    assert other.status == ScheduledEvent.Status.SCHEDULED
    assert attempt.voice_action_digit == "1"
    assert attempt.voice_action_result == DeliveryAttempt.VoiceActionResult.CANCELLED
    assert attempt.voice_action_target_event_id == earlier.id
    assert attempt.voice_action_completed_at is not None


def test_digit_two_switches_earliest_pending_event_to_sms(
    client,
    voice_action_context,
):
    user, phone, attempt = voice_action_context
    target = create_pending_event(user, phone, channel=ScheduledEvent.Channel.VOICE)

    response = post_action(client, digit="2")

    attempt.refresh_from_db()
    target.refresh_from_db()
    assert response.status_code == 200
    assert "sent by text message" in response.content.decode()
    assert target.channel == ScheduledEvent.Channel.SMS
    assert target.status == ScheduledEvent.Status.SCHEDULED
    assert attempt.voice_action_result == (
        DeliveryAttempt.VoiceActionResult.SWITCHED_TO_SMS
    )
    assert attempt.voice_action_target_event_id == target.id


def test_duplicate_action_returns_recorded_result_without_touching_next_event(
    client,
    voice_action_context,
):
    user, phone, attempt = voice_action_context
    first_target = create_pending_event(user, phone)

    first = post_action(client, digit="1")
    second_target = create_pending_event(user, phone, hours=2)
    duplicate = post_action(client, digit="2")

    attempt.refresh_from_db()
    first_target.refresh_from_db()
    second_target.refresh_from_db()
    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert "has been cancelled" in duplicate.content.decode()
    assert first_target.status == ScheduledEvent.Status.CANCELLED
    assert second_target.status == ScheduledEvent.Status.SCHEDULED
    assert second_target.channel == ScheduledEvent.Channel.VOICE
    assert attempt.voice_action_digit == "1"
    assert attempt.voice_action_target_event_id == first_target.id


def test_valid_action_with_no_pending_event_is_audited_and_idempotent(
    client,
    voice_action_context,
):
    _user, _phone, attempt = voice_action_context

    first = post_action(client, digit="1")
    duplicate = post_action(client, digit="1")

    attempt.refresh_from_db()
    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert "do not have a pending" in first.content.decode()
    assert attempt.voice_action_result == (
        DeliveryAttempt.VoiceActionResult.NO_PENDING_EVENT
    )
    assert attempt.voice_action_target_event_id is None


@pytest.mark.parametrize("digit", ["0", "3", "9"])
def test_invalid_digit_reprompts_without_recording_action(
    client,
    voice_action_context,
    digit,
):
    _user, _phone, attempt = voice_action_context

    response = post_action(client, digit=digit)

    attempt.refresh_from_db()
    content = response.content.decode()
    assert response.status_code == 200
    assert "not recognized" in content
    assert "Press 1 to cancel" in content
    assert ACTION_URL in content
    assert attempt.voice_action_result == ""


def test_malformed_signed_action_returns_safe_reprompt(client, voice_action_context):
    _user, _phone, attempt = voice_action_context

    response = post_action(client, digit="12")

    attempt.refresh_from_db()
    assert response.status_code == 200
    assert "could not be understood" in response.content.decode()
    assert attempt.voice_action_result == ""


def test_action_rejects_invalid_signature(client, voice_action_context):
    _user, _phone, attempt = voice_action_context

    response = post_action(client, signed=False)

    attempt.refresh_from_db()
    assert response.status_code == 403
    assert attempt.voice_action_result == ""


def test_unknown_or_stale_call_returns_safe_twiml(client, voice_action_context):
    response = post_action(client, call_sid="CA" + "d" * 32)

    assert response.status_code == 200
    assert "can no longer change" in response.content.decode()


def test_action_endpoint_is_post_only(client, voice_action_context):
    response = client.get(reverse("twilio-voice-action"))

    assert response.status_code == 405
