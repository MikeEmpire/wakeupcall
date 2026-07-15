from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.forms import PhoneEnrollmentForm, VerificationCodeForm
from apps.accounts.models import PhoneNumber
from apps.accounts.serializers import mask_phone_number
from apps.accounts.services import (
    check_user_phone_verification,
    create_phone_number,
    start_user_phone_verification,
)
from apps.accounts.verification import VerificationStatus
from apps.accounts.views import (
    VERIFICATION_API_ERRORS,
    get_phone_verification_gateway,
)
from apps.accounts.verification_exceptions import PhoneAlreadyVerified


def _verification_allowed(request, scope):
    throttle = ScopedRateThrottle()
    view = type("VerificationScope", (), {"throttle_scope": scope})()
    return throttle.allow_request(request, view)


def _verification_error_message(exc):
    if isinstance(exc, PhoneAlreadyVerified):
        return "This phone number is already verified."
    return "Verification could not be completed. Please try again later."


@login_required
@require_GET
def phone_list(request):
    phones = PhoneNumber.objects.filter(user=request.user)
    return render(
        request,
        "accounts/phone_list.html",
        {
            "phone_rows": [
                (phone, mask_phone_number(phone.number)) for phone in phones
            ]
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def phone_enroll(request):
    form = PhoneEnrollmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            phone = create_phone_number(
                user=request.user,
                number=form.cleaned_data["number"],
            )
        except DjangoValidationError:
            form.add_error("number", "This phone number cannot be enrolled.")
        else:
            messages.success(request, "Phone enrolled. Verify it before scheduling.")
            return redirect("accounts_web:phone-verify", phone_number_id=phone.id)

    return render(
        request,
        "accounts/phone_enroll.html",
        {"form": form},
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def phone_verify(request, phone_number_id):
    phone = get_object_or_404(
        PhoneNumber,
        id=phone_number_id,
        user=request.user,
    )
    form = VerificationCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not _verification_allowed(request, "phone_verification_check"):
            form.add_error(None, "Too many verification attempts. Try again later.")
        else:
            try:
                result = check_user_phone_verification(
                    phone.id,
                    form.cleaned_data["code"],
                    user=request.user,
                    gateway=(
                        None
                        if phone.is_verified
                        else get_phone_verification_gateway()
                    ),
                )
            except PhoneNumber.DoesNotExist:
                raise Http404 from None
            except VERIFICATION_API_ERRORS as exc:
                form.add_error(None, _verification_error_message(exc))
            else:
                if result.status == VerificationStatus.APPROVED:
                    messages.success(request, "Phone verified and ready to schedule.")
                    return redirect("accounts_web:phone-list")
                form.add_error(None, "That code was not approved. Check it and try again.")

    phone.refresh_from_db()
    return render(
        request,
        "accounts/phone_verify.html",
        {
            "phone": phone,
            "masked_number": mask_phone_number(phone.number),
            "form": form,
        },
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_POST
def phone_verification_start(request, phone_number_id):
    phone = get_object_or_404(
        PhoneNumber,
        id=phone_number_id,
        user=request.user,
    )
    if not _verification_allowed(request, "phone_verification_start"):
        messages.error(request, "Too many verification starts. Try again later.")
        return redirect("accounts_web:phone-verify", phone_number_id=phone.id)

    try:
        result = start_user_phone_verification(
            phone.id,
            user=request.user,
            gateway=(
                None if phone.is_verified else get_phone_verification_gateway()
            ),
        )
    except PhoneNumber.DoesNotExist:
        raise Http404 from None
    except VERIFICATION_API_ERRORS as exc:
        messages.error(request, _verification_error_message(exc))
    else:
        if result.status == VerificationStatus.PENDING:
            messages.success(request, "Verification code requested.")
        else:
            messages.info(request, "Verification request completed.")
    return redirect("accounts_web:phone-verify", phone_number_id=phone.id)
