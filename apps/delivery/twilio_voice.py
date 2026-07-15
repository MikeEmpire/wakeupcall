import logging
import re
from urllib.parse import urlparse

import requests
from django.conf import settings
from twilio.base.exceptions import TwilioRestException
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse

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
CALL_SID_PATTERN = re.compile(r"^CA[0-9a-fA-F]{32}$")


class TwilioVoiceSender:
    """Place weather-announcement calls using inline, escaped TwiML."""

    RATE_LIMIT_CODES = {20429}
    AUTHENTICATION_CODES = {20003, 20005, 20403}
    CONFIGURATION_CODES = {21210, 21212}
    INVALID_REQUEST_CODES = {21211, 21214, 21217}
    REJECTION_CODES = {21215, 21216}

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        status_callback_url: str,
        action_callback_url: str,
        timeout: float = 5.0,
        client=None,
    ):
        if not account_sid or not auth_token or not from_number:
            raise DeliveryConfigurationError(
                "Twilio account SID, auth token, and voice from number are required."
            )
        if not E164_PATTERN.fullmatch(from_number):
            raise DeliveryConfigurationError(
                "The Twilio voice from number must use E.164 format."
            )
        parsed_callback = urlparse(status_callback_url)
        if parsed_callback.scheme != "https" or not parsed_callback.netloc:
            raise DeliveryConfigurationError(
                "The Twilio voice status callback must be an absolute HTTPS URL."
            )
        parsed_action_callback = urlparse(action_callback_url)
        if (
            parsed_action_callback.scheme != "https"
            or not parsed_action_callback.netloc
        ):
            raise DeliveryConfigurationError(
                "The Twilio voice action callback must be an absolute HTTPS URL."
            )
        if timeout <= 0:
            raise DeliveryConfigurationError("Twilio timeout must be positive.")

        self.from_number = from_number
        self.status_callback_url = status_callback_url
        self.action_callback_url = action_callback_url
        if client is None:
            http_client = TwilioHttpClient(timeout=timeout)
            client = Client(account_sid, auth_token, http_client=http_client)
        self.calls = client.calls

    @classmethod
    def from_settings(cls):
        return cls(
            account_sid=settings.TWILIO_ACCOUNT_SID,
            auth_token=settings.TWILIO_AUTH_TOKEN,
            from_number=settings.TWILIO_VOICE_FROM_NUMBER,
            status_callback_url=settings.TWILIO_VOICE_STATUS_CALLBACK_URL,
            action_callback_url=settings.TWILIO_VOICE_ACTION_CALLBACK_URL,
            timeout=settings.TWILIO_HTTP_TIMEOUT,
        )

    def send(self, *, channel: str, to: str, message: str) -> DeliveryResult:
        self._validate_request(channel=channel, to=to, message=message)
        response = VoiceResponse()
        response.say(message)
        gather = response.gather(
            input="dtmf",
            num_digits=1,
            timeout=5,
            action=self.action_callback_url,
            method="POST",
        )
        gather.say(
            "Press 1 to cancel your next scheduled wake-up. "
            "Press 2 to receive it by text message instead."
        )
        response.say("No selection received. Goodbye.")

        try:
            call = self.calls.create(
                to=to,
                from_=self.from_number,
                twiml=str(response),
                status_callback=self.status_callback_url,
                status_callback_event=[
                    "initiated",
                    "ringing",
                    "answered",
                    "completed",
                ],
                status_callback_method="POST",
            )
        except TwilioRestException as exc:
            raise self._map_rest_error(exc) from None
        except requests.Timeout:
            raise DeliveryProviderTimeout(
                "The voice provider timed out before confirming submission."
            ) from None
        except requests.RequestException:
            raise DeliveryProviderUnavailable(
                "The voice provider could not be reached."
            ) from None

        provider_sid = getattr(call, "sid", None)
        if not isinstance(provider_sid, str) or not CALL_SID_PATTERN.fullmatch(
            provider_sid
        ):
            raise DeliveryMalformedResponse(
                "The voice provider returned an invalid call identifier."
            )

        logger.info(
            "Voice call submitted: provider=twilio to=%s provider_sid=%s",
            mask_phone_number(to),
            provider_sid,
        )
        return DeliveryResult(provider_sid=provider_sid)

    @staticmethod
    def _validate_request(*, channel: str, to: str, message: str) -> None:
        if channel != "voice":
            raise DeliveryInputInvalid("The Twilio voice sender only supports voice.")
        if not isinstance(to, str) or not E164_PATTERN.fullmatch(to):
            raise DeliveryInputInvalid(
                "The voice destination must use E.164 format."
            )
        if not isinstance(message, str) or not message or len(message) > 1600:
            raise DeliveryInputInvalid(
                "The voice announcement must contain between 1 and 1600 characters."
            )

    @classmethod
    def _map_rest_error(cls, exc: TwilioRestException) -> DeliveryError:
        code = exc.code
        status = exc.status

        if code in cls.RATE_LIMIT_CODES or status == 429:
            return DeliveryRateLimited(
                "The voice provider rate limit was reached. Try again later."
            )
        if code in cls.AUTHENTICATION_CODES or status in {401, 403}:
            return DeliveryAuthenticationError(
                "The voice provider credentials or access are invalid."
            )
        if code in cls.CONFIGURATION_CODES:
            return DeliveryConfigurationError(
                "The voice sender configuration was rejected by the provider."
            )
        if code in cls.REJECTION_CODES:
            return DeliveryProviderRejected(
                "The voice provider rejected the request."
            )
        if code in cls.INVALID_REQUEST_CODES or status == 400:
            return DeliveryInputInvalid(
                "The voice destination or request was invalid."
            )
        if status is not None and status >= 500:
            return DeliveryProviderUnavailable(
                "The voice provider is temporarily unavailable."
            )
        return DeliveryProviderRejected("The voice provider rejected the request.")
