from unittest.mock import Mock

import pytest
import requests
from django.test import override_settings
from twilio.base.exceptions import TwilioRestException

from apps.accounts.twilio_verify import TwilioVerifyGateway
from apps.accounts.verification import VerificationStatus
from apps.accounts.verification_exceptions import (
    VerificationAuthenticationError,
    VerificationBlocked,
    VerificationConfigurationError,
    VerificationExpired,
    VerificationInputInvalid,
    VerificationMalformedResponse,
    VerificationProviderTimeout,
    VerificationProviderUnavailable,
    VerificationRateLimited,
)


@pytest.fixture
def service():
    service = Mock()
    service.verifications.create.return_value = Mock(status="pending", sid="VE123")
    service.verification_checks.create.return_value = Mock(
        status="approved",
        sid="VE123",
    )
    return service


@pytest.fixture
def client(service):
    client = Mock()
    client.verify.v2.services.return_value = service
    return client


@pytest.fixture
def gateway(client):
    return TwilioVerifyGateway(
        account_sid="AC123",
        auth_token="secret-token",
        service_sid="VA123",
        timeout=3.0,
        client=client,
    )


def rest_error(*, status, code):
    return TwilioRestException(
        status=status,
        uri="https://verify.twilio.test",
        msg="provider detail that must not escape",
        code=code,
    )


def test_starts_sms_verification(gateway, service):
    result = gateway.start_verification("+14155552671")

    assert result.status == VerificationStatus.PENDING
    assert result.provider_sid == "VE123"
    service.verifications.create.assert_called_once_with(
        to="+14155552671",
        channel="sms",
    )


def test_checks_verification_code(gateway, service):
    result = gateway.check_verification("+14155552671", "123456")

    assert result.status == VerificationStatus.APPROVED
    service.verification_checks.create.assert_called_once_with(
        to="+14155552671",
        code="123456",
    )


@pytest.mark.parametrize(
    "twilio_status",
    ["canceled", "deleted", "expired", "failed", "max_attempts_reached"],
)
def test_maps_terminal_provider_status_to_rejected(gateway, service, twilio_status):
    service.verification_checks.create.return_value = Mock(
        status=twilio_status,
        sid="VE123",
    )

    result = gateway.check_verification("+14155552671", "123456")

    assert result.status == VerificationStatus.REJECTED


def test_rejects_unknown_provider_status(gateway, service):
    service.verifications.create.return_value = Mock(status="mystery", sid="VE123")

    with pytest.raises(VerificationMalformedResponse):
        gateway.start_verification("+14155552671")


@pytest.mark.parametrize(
    ("status", "code", "expected_exception"),
    [
        (429, 20429, VerificationRateLimited),
        (400, 60202, VerificationExpired),
        (400, 60238, VerificationBlocked),
        (404, 20404, VerificationExpired),
        (401, 20003, VerificationAuthenticationError),
        (400, 60200, VerificationInputInvalid),
        (503, 20503, VerificationProviderUnavailable),
    ],
)
def test_maps_twilio_errors_without_exposing_provider_details(
    gateway,
    service,
    status,
    code,
    expected_exception,
):
    service.verifications.create.side_effect = rest_error(status=status, code=code)

    with pytest.raises(expected_exception) as error:
        gateway.start_verification("+14155552671")

    assert "provider detail" not in str(error.value)


@pytest.mark.parametrize(
    ("provider_error", "expected_exception"),
    [
        (requests.Timeout("secret-token"), VerificationProviderTimeout),
        (requests.ConnectionError("secret-token"), VerificationProviderUnavailable),
    ],
)
def test_maps_network_errors_without_exposing_credentials(
    gateway,
    service,
    provider_error,
    expected_exception,
):
    service.verifications.create.side_effect = provider_error

    with pytest.raises(expected_exception) as error:
        gateway.start_verification("+14155552671")

    assert "secret-token" not in str(error.value)


def test_rejects_incomplete_configuration():
    with pytest.raises(VerificationConfigurationError):
        TwilioVerifyGateway(
            account_sid="",
            auth_token="",
            service_sid="",
        )


@override_settings(
    TWILIO_ACCOUNT_SID="ACsettings",
    TWILIO_AUTH_TOKEN="settings-token",
    TWILIO_VERIFY_SERVICE_SID="VAsettings",
    TWILIO_HTTP_TIMEOUT=4.0,
)
def test_builds_gateway_from_django_settings(monkeypatch):
    client_class = Mock()
    monkeypatch.setattr("apps.accounts.twilio_verify.Client", client_class)

    gateway = TwilioVerifyGateway.from_settings()

    assert gateway.service_sid == "VAsettings"
    client_class.assert_called_once()
