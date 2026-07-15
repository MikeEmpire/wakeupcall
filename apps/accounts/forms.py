from django import forms

from apps.accounts.models import e164_validator


class PhoneEnrollmentForm(forms.Form):
    number = forms.CharField(
        label="Phone number",
        max_length=16,
        validators=[e164_validator],
        help_text="Use E.164 format, including country code, such as +14155552671.",
        widget=forms.TextInput(
            attrs={"autocomplete": "tel", "placeholder": "+14155552671"}
        ),
    )


class VerificationCodeForm(forms.Form):
    code = forms.RegexField(
        label="Verification code",
        regex=r"^\d{4,10}$",
        error_messages={
            "invalid": "The verification code must contain 4 to 10 digits."
        },
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "maxlength": "10",
            }
        ),
    )
