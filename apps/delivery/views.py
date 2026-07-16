from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.delivery.services import (
    DeliveryAttemptNotFound,
    apply_inbound_sms_command,
    apply_voice_menu_action,
    record_voice_status_callback,
)
from apps.delivery.twilio_webhooks import (
    InvalidTwilioSignature,
    MalformedInboundSmsCallback,
    MalformedVoiceActionCallback,
    MalformedVoiceStatusCallback,
    TwilioVoiceActionWebhook,
    TwilioVoiceStatusWebhook,
    TwilioInboundSmsWebhook,
)
from apps.delivery.models import DeliveryAttempt, InboundSmsCommand
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse


def _voice_menu_twiml(*, prompt=None):
    response = VoiceResponse()
    if prompt:
        response.say(prompt)
    gather = response.gather(
        input="dtmf",
        num_digits=1,
        timeout=5,
        action=settings.TWILIO_VOICE_ACTION_CALLBACK_URL,
        method="POST",
    )
    gather.say(
        "Press 1 to cancel your next scheduled wake-up. "
        "Press 2 to receive it by text message instead."
    )
    response.say("No selection received. Goodbye.")
    return str(response)


def _voice_action_result_twiml(outcome):
    response = VoiceResponse()
    messages = {
        DeliveryAttempt.VoiceActionResult.CANCELLED: (
            "Your next scheduled wake-up has been cancelled. Goodbye."
        ),
        DeliveryAttempt.VoiceActionResult.SWITCHED_TO_SMS: (
            "Your next scheduled wake-up will be sent by text message. Goodbye."
        ),
        DeliveryAttempt.VoiceActionResult.NO_PENDING_EVENT: (
            "You do not have a pending wake-up event. Goodbye."
        ),
    }
    response.say(messages[outcome])
    return str(response)


def _inbound_sms_result_twiml(outcome, *, suppress_message=False):
    response = MessagingResponse()
    if suppress_message:
        return str(response)
    messages = {
        InboundSmsCommand.Result.CANCELLED: "Next wake-up cancelled.",
        InboundSmsCommand.Result.SWITCHED_TO_SMS: "Next wake-up set to SMS.",
        InboundSmsCommand.Result.RESCHEDULED: "Next wake-up time updated.",
        InboundSmsCommand.Result.NO_PENDING_EVENT: "No pending wake-up.",
        InboundSmsCommand.Result.UNKNOWN_SENDER: "Request could not be processed.",
        InboundSmsCommand.Result.INVALID_COMMAND: (
            "Use STOP, SMS, or TIME followed by an ISO 8601 time with an offset."
        ),
        InboundSmsCommand.Result.INVALID_TIME: (
            "Use a future ISO 8601 time with an explicit offset."
        ),
        InboundSmsCommand.Result.LIFECYCLE_CONFLICT: (
            "That wake-up can no longer be changed."
        ),
    }
    response.message(messages[outcome])
    return str(response)


@csrf_exempt
@require_POST
def twilio_voice_status(request):
    try:
        callback = TwilioVoiceStatusWebhook(
            auth_token=settings.TWILIO_AUTH_TOKEN,
            callback_url=settings.TWILIO_VOICE_STATUS_CALLBACK_URL,
        ).parse(
            params=request.POST,
            signature=request.headers.get("X-Twilio-Signature", ""),
        )
    except InvalidTwilioSignature:
        return HttpResponse(status=403)
    except MalformedVoiceStatusCallback:
        return HttpResponse(status=400)

    try:
        record_voice_status_callback(
            provider_sid=callback.provider_sid,
            provider_status=callback.provider_status,
            sequence_number=callback.sequence_number,
        )
    except DeliveryAttemptNotFound:
        return HttpResponse(status=404)

    return HttpResponse(status=204)


@csrf_exempt
@require_POST
def twilio_voice_action(request):
    try:
        callback = TwilioVoiceActionWebhook(
            auth_token=settings.TWILIO_AUTH_TOKEN,
            callback_url=settings.TWILIO_VOICE_ACTION_CALLBACK_URL,
        ).parse(
            params=request.POST,
            signature=request.headers.get("X-Twilio-Signature", ""),
        )
    except InvalidTwilioSignature:
        return HttpResponse(status=403)
    except MalformedVoiceActionCallback:
        return HttpResponse(
            _voice_menu_twiml(prompt="That selection could not be understood."),
            content_type="text/xml",
        )

    if callback.digit not in {"1", "2"}:
        return HttpResponse(
            _voice_menu_twiml(prompt="That choice was not recognized."),
            content_type="text/xml",
        )

    try:
        result = apply_voice_menu_action(
            provider_sid=callback.provider_sid,
            digit=callback.digit,
        )
    except DeliveryAttemptNotFound:
        response = VoiceResponse()
        response.say("This call can no longer change a scheduled wake-up. Goodbye.")
        return HttpResponse(str(response), content_type="text/xml")

    return HttpResponse(
        _voice_action_result_twiml(result.outcome),
        content_type="text/xml",
    )


@csrf_exempt
@require_POST
def twilio_inbound_sms(request):
    try:
        callback = TwilioInboundSmsWebhook(
            auth_token=settings.TWILIO_AUTH_TOKEN,
            callback_url=settings.TWILIO_SMS_INBOUND_CALLBACK_URL,
            recipient=settings.TWILIO_SMS_FROM_NUMBER,
        ).parse(
            params=request.POST,
            signature=request.headers.get("X-Twilio-Signature", ""),
        )
    except InvalidTwilioSignature:
        return HttpResponse(status=403)
    except MalformedInboundSmsCallback:
        response = MessagingResponse()
        response.message("Request could not be processed.")
        return HttpResponse(str(response), content_type="text/xml")

    result = apply_inbound_sms_command(
        provider_sid=callback.provider_sid,
        sender=callback.sender,
        body=callback.body,
    )
    return HttpResponse(
        _inbound_sms_result_twiml(
            result.outcome,
            suppress_message=callback.opt_out_type == "STOP",
        ),
        content_type="text/xml",
    )
