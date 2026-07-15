from django.http import Http404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import PhoneNumber
from apps.accounts.serializers import (
    PhoneNumberSerializer,
    VerificationCheckSerializer,
)
from apps.accounts.services import (
    check_user_phone_verification,
    start_user_phone_verification,
)
from apps.accounts.twilio_verify import TwilioVerifyGateway
from apps.accounts.verification_exceptions import (
    PhoneAlreadyVerified,
    PhoneNumberChanged,
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


def get_phone_verification_gateway():
    return TwilioVerifyGateway.from_settings()


def _safe_phone_data(phone_number_id: int, *, user):
    phone_number = PhoneNumber.objects.get(id=phone_number_id, user=user)
    return PhoneNumberSerializer(phone_number).data


VERIFICATION_API_ERRORS = (
    PhoneAlreadyVerified,
    PhoneNumberChanged,
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


def _provider_error_response(exc):
    if isinstance(exc, (VerificationRateLimited, VerificationBlocked)):
        return Response(
            {"detail": "Verification is temporarily unavailable. Try again later."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    if isinstance(exc, (VerificationInputInvalid, VerificationExpired)):
        return Response(
            {"detail": "The verification challenge or code is invalid."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if isinstance(exc, (PhoneAlreadyVerified, PhoneNumberChanged)):
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    if isinstance(
        exc,
        (
            VerificationAuthenticationError,
            VerificationConfigurationError,
            VerificationMalformedResponse,
            VerificationProviderTimeout,
            VerificationProviderUnavailable,
        ),
    ):
        return Response(
            {"detail": "Verification is temporarily unavailable."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    raise exc


class PhoneNumberListCreateView(generics.ListCreateAPIView):
    serializer_class = PhoneNumberSerializer

    def get_queryset(self):
        return PhoneNumber.objects.filter(user=self.request.user)


class PhoneVerificationStartView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "phone_verification_start"

    def post(self, request, phone_number_id):
        try:
            phone_number = PhoneNumber.objects.get(
                id=phone_number_id,
                user=request.user,
            )
            result = start_user_phone_verification(
                phone_number_id,
                user=request.user,
                gateway=(
                    None
                    if phone_number.is_verified
                    else get_phone_verification_gateway()
                ),
            )
            phone_data = _safe_phone_data(phone_number_id, user=request.user)
        except PhoneNumber.DoesNotExist:
            raise Http404 from None
        except VERIFICATION_API_ERRORS as exc:
            return _provider_error_response(exc)

        return Response(
            {"verification_status": result.status, "phone": phone_data}
        )


class PhoneVerificationCheckView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "phone_verification_check"

    def post(self, request, phone_number_id):
        serializer = VerificationCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            phone_number = PhoneNumber.objects.get(
                id=phone_number_id,
                user=request.user,
            )
            result = check_user_phone_verification(
                phone_number_id,
                serializer.validated_data["code"],
                user=request.user,
                gateway=(
                    None
                    if phone_number.is_verified
                    else get_phone_verification_gateway()
                ),
            )
            phone_data = _safe_phone_data(phone_number_id, user=request.user)
        except PhoneNumber.DoesNotExist:
            raise Http404 from None
        except VERIFICATION_API_ERRORS as exc:
            return _provider_error_response(exc)

        return Response(
            {"verification_status": result.status, "phone": phone_data}
        )
