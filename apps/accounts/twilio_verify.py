import requests
from django.conf import settings
from twilio.base.exceptions import TwilioRestException
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client

from apps.accounts.verification import VerificationResult, VerificationStatus
from apps.accounts.verification_exceptions import (
    VerificationAuthenticationError,
    VerificationBlocked,
    VerificationConfigurationError,
    VerificationError,
    VerificationExpired,
    VerificationInputInvalid,
    VerificationMalformedResponse,
    VerificationProviderTimeout,
    VerificationProviderUnavailable,
    VerificationRateLimited,
)


class TwilioVerifyGateway:
    RATE_LIMIT_CODES = {20429, 60203, 60207, 60212, 60624, 60626, 60728}
    BLOCKED_CODES = {60238, 60410, 60412, 60605}
    AUTHENTICATION_CODES = {20003, 20403, 60361}
    EXPIRED_CODES = {20404, 60202, 60623}
    REJECTED_STATUSES = {
        "canceled",
        "deleted",
        "expired",
        "failed",
        "max_attempts_reached",
    }

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        service_sid: str,
        timeout: float = 5.0,
        client=None,
    ):
        if not account_sid or not auth_token or not service_sid:
            raise VerificationConfigurationError(
                "Twilio account SID, auth token, and Verify service SID are required."
            )
        if timeout <= 0:
            raise VerificationConfigurationError("Twilio timeout must be positive.")

        self.service_sid = service_sid
        if client is None:
            http_client = TwilioHttpClient(timeout=timeout)
            client = Client(account_sid, auth_token, http_client=http_client)
        self.service = client.verify.v2.services(service_sid)

    @classmethod
    def from_settings(cls):
        return cls(
            account_sid=settings.TWILIO_ACCOUNT_SID,
            auth_token=settings.TWILIO_AUTH_TOKEN,
            service_sid=settings.TWILIO_VERIFY_SERVICE_SID,
            timeout=settings.TWILIO_HTTP_TIMEOUT,
        )

    def start_verification(self, phone_number: str) -> VerificationResult:
        try:
            verification = self.service.verifications.create(
                to=phone_number,
                channel="sms",
            )
        except TwilioRestException as exc:
            raise self._map_rest_error(exc) from None
        except requests.Timeout:
            raise VerificationProviderTimeout(
                "The verification provider timed out."
            ) from None
        except requests.RequestException:
            raise VerificationProviderUnavailable(
                "The verification provider could not be reached."
            ) from None

        return self._map_result(verification)

    def check_verification(
        self,
        phone_number: str,
        code: str,
    ) -> VerificationResult:
        try:
            verification = self.service.verification_checks.create(
                to=phone_number,
                code=code,
            )
        except TwilioRestException as exc:
            raise self._map_rest_error(exc) from None
        except requests.Timeout:
            raise VerificationProviderTimeout(
                "The verification provider timed out."
            ) from None
        except requests.RequestException:
            raise VerificationProviderUnavailable(
                "The verification provider could not be reached."
            ) from None

        return self._map_result(verification)

    @classmethod
    def _map_result(cls, verification) -> VerificationResult:
        status = getattr(verification, "status", None)
        provider_sid = getattr(verification, "sid", None)

        if status == "pending":
            normalized_status = VerificationStatus.PENDING
        elif status == "approved":
            normalized_status = VerificationStatus.APPROVED
        elif status in cls.REJECTED_STATUSES:
            normalized_status = VerificationStatus.REJECTED
        else:
            raise VerificationMalformedResponse(
                "The verification provider returned an unknown status."
            )

        return VerificationResult(
            status=normalized_status,
            provider_sid=provider_sid,
        )

    @classmethod
    def _map_rest_error(cls, exc: TwilioRestException) -> VerificationError:
        code = exc.code
        status = exc.status

        if code in cls.RATE_LIMIT_CODES or status == 429:
            return VerificationRateLimited(
                "Too many verification attempts. Try again later."
            )
        if code in cls.BLOCKED_CODES:
            return VerificationBlocked(
                "The verification provider blocked this attempt."
            )
        if code in cls.EXPIRED_CODES or status == 404:
            return VerificationExpired(
                "The verification challenge expired or was not found."
            )
        if code in cls.AUTHENTICATION_CODES or status in {401, 403}:
            return VerificationAuthenticationError(
                "The verification provider credentials or access are invalid."
            )
        if code == 60200 or status == 400:
            return VerificationInputInvalid(
                "The phone number or verification code was invalid."
            )
        if status is not None and status >= 500:
            return VerificationProviderUnavailable(
                "The verification provider is temporarily unavailable."
            )
        return VerificationProviderUnavailable(
            "The verification provider rejected the request."
        )
