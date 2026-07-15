from unittest.mock import Mock

import pytest
import requests
from django.test import override_settings
from twilio.base.exceptions import TwilioRestException

from apps.delivery.exceptions import (
    DeliveryAuthenticationError,
    DeliveryConfigurationError,
    DeliveryInputInvalid,
    DeliveryMalformedResponse,
    DeliveryProviderRejected,
    DeliveryProviderTimeout,
    DeliveryProviderUnavailable,
    DeliveryRateLimited,
)
from apps.delivery.twilio_voice import TwilioVoiceSender

CALL_SID = "CA0123456789abcdef0123456789abcdef"
CALLBACK_URL = "https://example.test/twilio/voice/status/"


@pytest.fixture
def calls():
    calls = Mock()
    calls.create.return_value = Mock(sid=CALL_SID)
    return calls


@pytest.fixture
def sender(calls):
    client = Mock(calls=calls)
    return TwilioVoiceSender(
        account_sid="AC123",
        auth_token="secret-token",
        from_number="+14155550100",
        status_callback_url=CALLBACK_URL,
        timeout=3.0,
        client=client,
    )


def rest_error(*, status, code):
    return TwilioRestException(
        status=status,
        uri="https://api.twilio.test",
        msg="provider detail with secret-token that must not escape",
        code=code,
    )


def test_places_call_with_safe_inline_twiml_and_callbacks(sender, calls):
    result = sender.send(
        channel="voice",
        to="+14155552671",
        message="Weather is <clear> & warm.",
    )

    assert result.provider_sid == CALL_SID
    kwargs = calls.create.call_args.kwargs
    assert kwargs["to"] == "+14155552671"
    assert kwargs["from_"] == "+14155550100"
    assert "&lt;clear&gt; &amp; warm." in kwargs["twiml"]
    assert kwargs["status_callback"] == CALLBACK_URL
    assert kwargs["status_callback_method"] == "POST"
    assert kwargs["status_callback_event"] == [
        "initiated",
        "ringing",
        "answered",
        "completed",
    ]


@pytest.mark.parametrize(
    ("channel", "to", "message"),
    [
        ("sms", "+14155552671", "message"),
        ("voice", "4155552671", "message"),
        ("voice", "+14155552671", ""),
        ("voice", "+14155552671", "x" * 1601),
    ],
)
def test_rejects_invalid_request_before_client_call(
    sender,
    calls,
    channel,
    to,
    message,
):
    with pytest.raises(DeliveryInputInvalid):
        sender.send(channel=channel, to=to, message=message)

    calls.create.assert_not_called()


@pytest.mark.parametrize(
    ("status", "code", "expected_exception"),
    [
        (400, 21211, DeliveryInputInvalid),
        (401, 20003, DeliveryAuthenticationError),
        (400, 21210, DeliveryConfigurationError),
        (429, 20429, DeliveryRateLimited),
        (400, 21216, DeliveryProviderRejected),
        (503, 20503, DeliveryProviderUnavailable),
    ],
)
def test_maps_twilio_errors_without_exposing_provider_details(
    sender,
    calls,
    status,
    code,
    expected_exception,
):
    calls.create.side_effect = rest_error(status=status, code=code)

    with pytest.raises(expected_exception) as error:
        sender.send(channel="voice", to="+14155552671", message="message")

    assert "provider detail" not in str(error.value)
    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize(
    ("provider_error", "expected_exception"),
    [
        (requests.Timeout("secret-token"), DeliveryProviderTimeout),
        (requests.ConnectionError("secret-token"), DeliveryProviderUnavailable),
    ],
)
def test_maps_network_errors_without_exposing_details(
    sender,
    calls,
    provider_error,
    expected_exception,
):
    calls.create.side_effect = provider_error

    with pytest.raises(expected_exception) as error:
        sender.send(channel="voice", to="+14155552671", message="message")

    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize("sid", [None, "", "CA123", "SM" + "0" * 32])
def test_rejects_malformed_call_sid(sender, calls, sid):
    calls.create.return_value = Mock(sid=sid)

    with pytest.raises(DeliveryMalformedResponse):
        sender.send(channel="voice", to="+14155552671", message="message")


@pytest.mark.parametrize(
    "configuration",
    [
        {"account_sid": "", "auth_token": "", "from_number": ""},
        {"from_number": "not-e164"},
        {"status_callback_url": "http://example.test/status/"},
        {"timeout": 0},
    ],
)
def test_rejects_invalid_configuration(configuration):
    values = {
        "account_sid": "AC123",
        "auth_token": "token",
        "from_number": "+14155550100",
        "status_callback_url": CALLBACK_URL,
    }
    values.update(configuration)

    with pytest.raises(DeliveryConfigurationError):
        TwilioVoiceSender(**values)


@override_settings(
    TWILIO_ACCOUNT_SID="ACsettings",
    TWILIO_AUTH_TOKEN="settings-token",
    TWILIO_VOICE_FROM_NUMBER="+14155550100",
    TWILIO_VOICE_STATUS_CALLBACK_URL=CALLBACK_URL,
    TWILIO_HTTP_TIMEOUT=4.0,
)
def test_builds_sender_from_settings_with_bounded_timeout(monkeypatch):
    http_client_class = Mock()
    client_class = Mock()
    monkeypatch.setattr("apps.delivery.twilio_voice.TwilioHttpClient", http_client_class)
    monkeypatch.setattr("apps.delivery.twilio_voice.Client", client_class)

    sender = TwilioVoiceSender.from_settings()

    http_client_class.assert_called_once_with(timeout=4.0)
    client_class.assert_called_once_with(
        "ACsettings",
        "settings-token",
        http_client=http_client_class.return_value,
    )
    assert sender.status_callback_url == CALLBACK_URL


def test_success_log_masks_destination_and_omits_announcement(sender, caplog):
    caplog.set_level("INFO", logger="apps.delivery.twilio_voice")

    sender.send(
        channel="voice",
        to="+14155552671",
        message="sensitive announcement",
    )

    assert "+14155552671" not in caplog.text
    assert "sensitive announcement" not in caplog.text
