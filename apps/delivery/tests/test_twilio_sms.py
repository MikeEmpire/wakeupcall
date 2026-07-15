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
from apps.delivery.twilio_sms import TwilioSmsSender

MESSAGE_SID = "SM0123456789abcdef0123456789abcdef"


@pytest.fixture
def messages():
    messages = Mock()
    messages.create.return_value = Mock(sid=MESSAGE_SID)
    return messages


@pytest.fixture
def client(messages):
    return Mock(messages=messages)


@pytest.fixture
def sender(client):
    return TwilioSmsSender(
        account_sid="AC123",
        auth_token="secret-token",
        from_number="+14155550100",
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


def test_submits_sms_and_maps_message_sid(sender, messages):
    result = sender.send(
        channel="sms",
        to="+14155552671",
        message="Weather wake-up message",
    )

    assert result.provider_sid == MESSAGE_SID
    messages.create.assert_called_once_with(
        to="+14155552671",
        from_="+14155550100",
        body="Weather wake-up message",
    )


@pytest.mark.parametrize(
    ("channel", "to", "message"),
    [
        ("voice", "+14155552671", "message"),
        ("sms", "4155552671", "message"),
        ("sms", "+14155552671", ""),
        ("sms", "+14155552671", "x" * 1601),
    ],
)
def test_rejects_invalid_local_request_before_client_call(
    sender,
    messages,
    channel,
    to,
    message,
):
    with pytest.raises(DeliveryInputInvalid):
        sender.send(channel=channel, to=to, message=message)

    messages.create.assert_not_called()


@pytest.mark.parametrize(
    ("status", "code", "expected_exception"),
    [
        (400, 21211, DeliveryInputInvalid),
        (401, 20003, DeliveryAuthenticationError),
        (400, 21606, DeliveryConfigurationError),
        (429, 20429, DeliveryRateLimited),
        (400, 21610, DeliveryProviderRejected),
        (400, 21611, DeliveryProviderUnavailable),
        (422, 30007, DeliveryProviderRejected),
        (503, 20503, DeliveryProviderUnavailable),
    ],
)
def test_maps_twilio_errors_without_exposing_provider_details(
    sender,
    messages,
    status,
    code,
    expected_exception,
):
    messages.create.side_effect = rest_error(status=status, code=code)

    with pytest.raises(expected_exception) as error:
        sender.send(channel="sms", to="+14155552671", message="message")

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
    messages,
    provider_error,
    expected_exception,
):
    messages.create.side_effect = provider_error

    with pytest.raises(expected_exception) as error:
        sender.send(channel="sms", to="+14155552671", message="message")

    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize("sid", [None, "", "SM123", "CA" + "0" * 32])
def test_rejects_malformed_provider_message_sid(sender, messages, sid):
    messages.create.return_value = Mock(sid=sid)

    with pytest.raises(DeliveryMalformedResponse):
        sender.send(channel="sms", to="+14155552671", message="message")


def test_rejects_incomplete_or_invalid_configuration():
    with pytest.raises(DeliveryConfigurationError):
        TwilioSmsSender(account_sid="", auth_token="", from_number="")

    with pytest.raises(DeliveryConfigurationError):
        TwilioSmsSender(
            account_sid="AC123",
            auth_token="token",
            from_number="not-e164",
        )

    with pytest.raises(DeliveryConfigurationError):
        TwilioSmsSender(
            account_sid="AC123",
            auth_token="token",
            from_number="+14155550100",
            timeout=0,
        )


@override_settings(
    TWILIO_ACCOUNT_SID="ACsettings",
    TWILIO_AUTH_TOKEN="settings-token",
    TWILIO_SMS_FROM_NUMBER="+14155550100",
    TWILIO_HTTP_TIMEOUT=4.0,
)
def test_builds_sender_from_settings_with_bounded_timeout(monkeypatch):
    http_client_class = Mock()
    client_class = Mock()
    monkeypatch.setattr("apps.delivery.twilio_sms.TwilioHttpClient", http_client_class)
    monkeypatch.setattr("apps.delivery.twilio_sms.Client", client_class)

    sender = TwilioSmsSender.from_settings()

    http_client_class.assert_called_once_with(timeout=4.0)
    client_class.assert_called_once_with(
        "ACsettings",
        "settings-token",
        http_client=http_client_class.return_value,
    )
    assert sender.from_number == "+14155550100"


def test_success_log_masks_destination_and_omits_message(
    sender,
    caplog,
):
    caplog.set_level("INFO", logger="apps.delivery.twilio_sms")

    sender.send(
        channel="sms",
        to="+14155552671",
        message="sensitive message body",
    )

    assert "*******2671" in caplog.text
    assert "+14155552671" not in caplog.text
    assert "sensitive message body" not in caplog.text
