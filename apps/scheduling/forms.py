from datetime import UTC

from django import forms
from django.core.validators import RegexValidator
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.models import PhoneNumber
from apps.accounts.serializers import mask_phone_number
from apps.scheduling.models import ScheduledEvent


class ExplicitOffsetDateTimeField(forms.CharField):
    default_error_messages = {
        "invalid": "Enter a valid ISO 8601 date and time.",
        "timezone": "Include an explicit UTC offset, such as Z or +00:00.",
        "future": "The scheduled time must be in the future.",
    }

    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            "widget",
            forms.TextInput(
                attrs={
                    "autocomplete": "off",
                    "placeholder": "2026-07-17T14:30:00-07:00",
                }
            ),
        )
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        if not value:
            return None
        parsed = parse_datetime(value)
        if parsed is None:
            raise forms.ValidationError(self.error_messages["invalid"])
        if timezone.is_naive(parsed):
            raise forms.ValidationError(self.error_messages["timezone"])
        return parsed.astimezone(UTC)

    def validate(self, value):
        super().validate(value)
        if value is not None and value <= timezone.now():
            raise forms.ValidationError(self.error_messages["future"])


class MaskedPhoneChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return mask_phone_number(obj.number)


class ScheduledEventCreateForm(forms.Form):
    phone_number = MaskedPhoneChoiceField(
        queryset=PhoneNumber.objects.none(),
        empty_label="Choose a verified phone",
    )
    zip_code = forms.CharField(
        label="US ZIP code",
        max_length=5,
        validators=[
            RegexValidator(r"^\d{5}$", "Enter a five-digit US ZIP code.")
        ],
    )
    scheduled_for = ExplicitOffsetDateTimeField(
        label="Scheduled time",
        help_text="Use ISO 8601 with an explicit offset. Times are stored in UTC.",
    )
    channel = forms.ChoiceField(choices=ScheduledEvent.Channel.choices)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone_number"].queryset = PhoneNumber.objects.filter(
            user=user,
            verified_at__isnull=False,
        )


class RescheduleEventForm(forms.Form):
    scheduled_for = ExplicitOffsetDateTimeField(
        label="New scheduled time",
        help_text="Use ISO 8601 with an explicit offset. Times are stored in UTC.",
    )


class ChangeEventChannelForm(forms.Form):
    channel = forms.ChoiceField(choices=ScheduledEvent.Channel.choices)
