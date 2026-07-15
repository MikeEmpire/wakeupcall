from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from twilio.request_validator import RequestValidator

from apps.accounts.models import PhoneNumber
from apps.delivery.models import DeliveryAttempt
from apps.scheduling.models import ScheduledEvent

AUTH_TOKEN = "webhook-secret"
CALLBACK_URL = "https://wake.example.test/twilio/voice/status/"
CALL_SID = "CA" + "a" * 32


@pytest.fixture
def submitted_voice_attempt(db):
    user = get_user_model().objects.create_user(username="callback-user")
    phone = PhoneNumber.objects.create(
        user=user,
        number="+14155552671",
        verified_at=timezone.now(),
    )
    event = ScheduledEvent.objects.create(
        user=user,
        phone_number=phone,
        zip_code="94107",
        scheduled_for=timezone.now() - timedelta(minutes=1),
        channel=ScheduledEvent.Channel.VOICE,
        is_demo=False,
        status=ScheduledEvent.Status.SUBMITTED,
        completed_at=timezone.now(),
    )
    return DeliveryAttempt.objects.create(
        event=event,
        attempt_number=1,
        status=DeliveryAttempt.Status.SUBMITTED,
        provider_sid=CALL_SID,
        completed_at=timezone.now(),
    )


def signature(params):
    return RequestValidator(AUTH_TOKEN).compute_signature(CALLBACK_URL, params)


@pytest.mark.django_db
@override_settings(
    TWILIO_AUTH_TOKEN=AUTH_TOKEN,
    TWILIO_VOICE_STATUS_CALLBACK_URL=CALLBACK_URL,
)
def test_valid_callback_updates_attempt(client, submitted_voice_attempt):
    params = {
        "CallSid": CALL_SID,
        "CallStatus": "in-progress",
        "SequenceNumber": "2",
    }

    response = client.post(
        reverse("twilio-voice-status"),
        params,
        HTTP_X_TWILIO_SIGNATURE=signature(params),
    )

    submitted_voice_attempt.refresh_from_db()
    assert response.status_code == 204
    assert (
        submitted_voice_attempt.provider_status
        == DeliveryAttempt.ProviderStatus.IN_PROGRESS
    )
    assert submitted_voice_attempt.provider_status_sequence == 2


@pytest.mark.django_db
@override_settings(
    TWILIO_AUTH_TOKEN=AUTH_TOKEN,
    TWILIO_VOICE_STATUS_CALLBACK_URL=CALLBACK_URL,
)
def test_callback_rejects_invalid_signature(client, submitted_voice_attempt):
    response = client.post(
        reverse("twilio-voice-status"),
        {
            "CallSid": CALL_SID,
            "CallStatus": "completed",
            "SequenceNumber": "3",
        },
        HTTP_X_TWILIO_SIGNATURE="invalid",
    )

    submitted_voice_attempt.refresh_from_db()
    assert response.status_code == 403
    assert submitted_voice_attempt.provider_status == ""


@pytest.mark.django_db
@override_settings(
    TWILIO_AUTH_TOKEN=AUTH_TOKEN,
    TWILIO_VOICE_STATUS_CALLBACK_URL=CALLBACK_URL,
)
def test_callback_rejects_malformed_status(client, submitted_voice_attempt):
    params = {
        "CallSid": CALL_SID,
        "CallStatus": "mystery",
        "SequenceNumber": "3",
    }

    response = client.post(
        reverse("twilio-voice-status"),
        params,
        HTTP_X_TWILIO_SIGNATURE=signature(params),
    )

    assert response.status_code == 400


@pytest.mark.django_db
@override_settings(
    TWILIO_AUTH_TOKEN=AUTH_TOKEN,
    TWILIO_VOICE_STATUS_CALLBACK_URL=CALLBACK_URL,
)
def test_callback_returns_not_found_for_uncommitted_attempt(client):
    params = {
        "CallSid": "CA" + "b" * 32,
        "CallStatus": "initiated",
        "SequenceNumber": "0",
    }

    response = client.post(
        reverse("twilio-voice-status"),
        params,
        HTTP_X_TWILIO_SIGNATURE=signature(params),
    )

    assert response.status_code == 404
