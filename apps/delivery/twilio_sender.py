from apps.delivery.gateways import DeliveryResult
from apps.delivery.twilio_sms import TwilioSmsSender
from apps.delivery.twilio_voice import TwilioVoiceSender


class TwilioMessageSender:
    """Route a project message to its channel-specific Twilio adapter."""

    def __init__(self):
        self._sms_sender = None
        self._voice_sender = None

    def send(self, *, channel: str, to: str, message: str) -> DeliveryResult:
        if channel == "sms":
            if self._sms_sender is None:
                self._sms_sender = TwilioSmsSender.from_settings()
            return self._sms_sender.send(channel=channel, to=to, message=message)
        if channel == "voice":
            if self._voice_sender is None:
                self._voice_sender = TwilioVoiceSender.from_settings()
            return self._voice_sender.send(channel=channel, to=to, message=message)
        raise ValueError("Unsupported delivery channel.")
