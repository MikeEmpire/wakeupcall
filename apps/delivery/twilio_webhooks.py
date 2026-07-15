from dataclasses import dataclass
import re

from twilio.request_validator import RequestValidator

from apps.delivery.models import DeliveryAttempt


class InvalidTwilioSignature(RuntimeError):
    pass


class MalformedVoiceStatusCallback(RuntimeError):
    pass


class MalformedVoiceActionCallback(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceStatusCallback:
    provider_sid: str
    provider_status: str
    sequence_number: int


@dataclass(frozen=True)
class VoiceActionCallback:
    provider_sid: str
    digit: str


class TwilioVoiceActionWebhook:
    def __init__(self, *, auth_token: str, callback_url: str, validator=None):
        if not auth_token or not callback_url:
            raise MalformedVoiceActionCallback(
                "Twilio voice action validation is not configured."
            )
        self.callback_url = callback_url
        self.validator = validator or RequestValidator(auth_token)

    def parse(self, *, params, signature: str) -> VoiceActionCallback:
        if not signature or not self.validator.validate(
            self.callback_url,
            params,
            signature,
        ):
            raise InvalidTwilioSignature("Invalid Twilio webhook signature.")

        provider_sid = params.get("CallSid", "")
        digit = params.get("Digits", "")
        if not re.fullmatch(r"CA[0-9a-fA-F]{32}", provider_sid) or not re.fullmatch(
            r"\d", digit
        ):
            raise MalformedVoiceActionCallback(
                "The voice action callback was malformed."
            )
        return VoiceActionCallback(provider_sid=provider_sid, digit=digit)


class TwilioVoiceStatusWebhook:
    STATUS_MAP = {
        "queued": DeliveryAttempt.ProviderStatus.QUEUED,
        "initiated": DeliveryAttempt.ProviderStatus.INITIATED,
        "ringing": DeliveryAttempt.ProviderStatus.RINGING,
        "in-progress": DeliveryAttempt.ProviderStatus.IN_PROGRESS,
        "completed": DeliveryAttempt.ProviderStatus.COMPLETED,
        "busy": DeliveryAttempt.ProviderStatus.BUSY,
        "no-answer": DeliveryAttempt.ProviderStatus.NO_ANSWER,
        "failed": DeliveryAttempt.ProviderStatus.FAILED,
        "canceled": DeliveryAttempt.ProviderStatus.CANCELED,
    }

    def __init__(self, *, auth_token: str, callback_url: str, validator=None):
        if not auth_token or not callback_url:
            raise MalformedVoiceStatusCallback(
                "Twilio webhook validation is not configured."
            )
        self.callback_url = callback_url
        self.validator = validator or RequestValidator(auth_token)

    def parse(self, *, params, signature: str) -> VoiceStatusCallback:
        if not signature or not self.validator.validate(
            self.callback_url,
            params,
            signature,
        ):
            raise InvalidTwilioSignature("Invalid Twilio webhook signature.")

        provider_sid = params.get("CallSid", "")
        raw_status = params.get("CallStatus", "")
        raw_sequence = params.get("SequenceNumber", "")
        if not provider_sid.startswith("CA") or raw_status not in self.STATUS_MAP:
            raise MalformedVoiceStatusCallback(
                "The voice status callback was malformed."
            )
        try:
            sequence_number = int(raw_sequence)
        except (TypeError, ValueError):
            raise MalformedVoiceStatusCallback(
                "The voice status callback was malformed."
            ) from None
        if sequence_number < 0:
            raise MalformedVoiceStatusCallback(
                "The voice status callback was malformed."
            )

        return VoiceStatusCallback(
            provider_sid=provider_sid,
            provider_status=self.STATUS_MAP[raw_status],
            sequence_number=sequence_number,
        )
