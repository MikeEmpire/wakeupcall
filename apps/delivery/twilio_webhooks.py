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


class MalformedInboundSmsCallback(RuntimeError):
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


@dataclass(frozen=True)
class InboundSmsCallback:
    provider_sid: str
    sender: str
    body: str
    opt_out_type: str


class TwilioInboundSmsWebhook:
    def __init__(
        self,
        *,
        auth_token: str,
        callback_url: str,
        recipient: str,
        validator=None,
    ):
        if not auth_token or not callback_url or not recipient:
            raise MalformedInboundSmsCallback(
                "Twilio inbound SMS validation is not configured."
            )
        self.callback_url = callback_url
        self.recipient = recipient
        self.validator = validator or RequestValidator(auth_token)

    def parse(self, *, params, signature: str) -> InboundSmsCallback:
        if not signature or not self.validator.validate(
            self.callback_url,
            params,
            signature,
        ):
            raise InvalidTwilioSignature("Invalid Twilio webhook signature.")

        provider_sid = params.get("MessageSid", "")
        sender = params.get("From", "")
        recipient = params.get("To", "")
        body = params.get("Body", "")
        opt_out_type = params.get("OptOutType", "")
        if (
            not re.fullmatch(r"SM[0-9a-fA-F]{32}", provider_sid)
            or not re.fullmatch(r"\+[1-9]\d{1,14}", sender)
            or recipient != self.recipient
            or not isinstance(body, str)
            or len(body) > 1600
            or opt_out_type not in {"", "STOP", "START", "HELP"}
        ):
            raise MalformedInboundSmsCallback(
                "The inbound SMS callback was malformed."
            )
        return InboundSmsCallback(
            provider_sid=provider_sid,
            sender=sender,
            body=body,
            opt_out_type=opt_out_type,
        )


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
