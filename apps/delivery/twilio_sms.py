import logging
import re

import requests
from django.conf import settings
from twilio.base.exceptions import TwilioRestException
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client

from apps.delivery.exceptions import (
    DeliveryAuthenticationError,
    DeliveryConfigurationError,
    DeliveryError,
    DeliveryInputInvalid,
    DeliveryMalformedResponse,
    DeliveryProviderRejected,
    DeliveryProviderTimeout,
    DeliveryProviderUnavailable,
    DeliveryRateLimited,
)
from apps.delivery.gateways import DeliveryResult, mask_phone_number

logger = logging.getLogger(__name__)

E164_PATTERN = re.compile(r"^\+[1-9]\d{1,14}$")
MESSAGE_SID_PATTERN = re.compile(r"^(SM|MM)[0-9a-fA-F]{32}$")


class TwilioSmsSender:
    """Submit SMS messages while keeping Twilio objects inside the adapter."""

    RATE_LIMIT_CODES = {20429}
    AUTHENTICATION_CODES = {20003, 20005, 20403}
    CONFIGURATION_CODES = {21212, 21606, 21659}
    INVALID_REQUEST_CODES = {21211, 21614, 21617}
    REJECTION_CODES = {21408, 21608, 21610, 21612}
    UNAVAILABLE_CODES = {21611, 21702}

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        timeout: float = 5.0,
        client=None,
    ):
        if not account_sid or not auth_token or not from_number:
            raise DeliveryConfigurationError(
                "Twilio account SID, auth token, and SMS from number are required."
            )
        if not E164_PATTERN.fullmatch(from_number):
            raise DeliveryConfigurationError(
                "The Twilio SMS from number must use E.164 format."
            )
        if timeout <= 0:
            raise DeliveryConfigurationError("Twilio timeout must be positive.")

        self.from_number = from_number
        if client is None:
            http_client = TwilioHttpClient(timeout=timeout)
            client = Client(account_sid, auth_token, http_client=http_client)
        self.messages = client.messages

    @classmethod
    def from_settings(cls):
        return cls(
            account_sid=settings.TWILIO_ACCOUNT_SID,
            auth_token=settings.TWILIO_AUTH_TOKEN,
            from_number=settings.TWILIO_SMS_FROM_NUMBER,
            timeout=settings.TWILIO_HTTP_TIMEOUT,
        )

    def send(self, *, channel: str, to: str, message: str) -> DeliveryResult:
        self._validate_request(channel=channel, to=to, message=message)

        try:
            twilio_message = self.messages.create(
                to=to,
                from_=self.from_number,
                body=message,
            )
        except TwilioRestException as exc:
            raise self._map_rest_error(exc) from None
        except requests.Timeout:
            raise DeliveryProviderTimeout(
                "The SMS provider timed out before confirming submission."
            ) from None
        except requests.RequestException:
            raise DeliveryProviderUnavailable(
                "The SMS provider could not be reached."
            ) from None

        provider_sid = getattr(twilio_message, "sid", None)
        if not isinstance(provider_sid, str) or not MESSAGE_SID_PATTERN.fullmatch(
            provider_sid
        ):
            raise DeliveryMalformedResponse(
                "The SMS provider returned an invalid message identifier."
            )

        logger.info(
            "SMS submitted: provider=twilio to=%s provider_sid=%s",
            mask_phone_number(to),
            provider_sid,
        )
        return DeliveryResult(provider_sid=provider_sid)

    @staticmethod
    def _validate_request(*, channel: str, to: str, message: str) -> None:
        if channel != "sms":
            raise DeliveryInputInvalid("The Twilio SMS sender only supports SMS.")
        if not isinstance(to, str) or not E164_PATTERN.fullmatch(to):
            raise DeliveryInputInvalid(
                "The SMS destination must use E.164 format."
            )
        if not isinstance(message, str) or not message or len(message) > 1600:
            raise DeliveryInputInvalid(
                "The SMS message must contain between 1 and 1600 characters."
            )

    @classmethod
    def _map_rest_error(cls, exc: TwilioRestException) -> DeliveryError:
        code = exc.code
        status = exc.status

        if code in cls.RATE_LIMIT_CODES or status == 429:
            return DeliveryRateLimited(
                "The SMS provider rate limit was reached. Try again later."
            )
        if code in cls.AUTHENTICATION_CODES or status in {401, 403}:
            return DeliveryAuthenticationError(
                "The SMS provider credentials or access are invalid."
            )
        if code in cls.CONFIGURATION_CODES:
            return DeliveryConfigurationError(
                "The SMS sender configuration was rejected by the provider."
            )
        if code in cls.REJECTION_CODES:
            return DeliveryProviderRejected("The SMS provider rejected the request.")
        if code in cls.UNAVAILABLE_CODES:
            return DeliveryProviderUnavailable(
                "The SMS provider is temporarily unavailable."
            )
        if code in cls.INVALID_REQUEST_CODES or status == 400:
            return DeliveryInputInvalid(
                "The SMS destination or request was invalid."
            )
        if status is not None and status >= 500:
            return DeliveryProviderUnavailable(
                "The SMS provider is temporarily unavailable."
            )
        return DeliveryProviderRejected("The SMS provider rejected the request.")
