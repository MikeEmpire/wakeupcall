from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.delivery.services import (
    DeliveryAttemptNotFound,
    record_voice_status_callback,
)
from apps.delivery.twilio_webhooks import (
    InvalidTwilioSignature,
    MalformedVoiceStatusCallback,
    TwilioVoiceStatusWebhook,
)


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
