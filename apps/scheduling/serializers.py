from datetime import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from apps.accounts.models import PhoneNumber
from apps.scheduling.models import ScheduledEvent
from apps.scheduling.services import create_scheduled_event


class ExplicitTimezoneDateTimeField(serializers.DateTimeField):
    default_error_messages = {
        **serializers.DateTimeField.default_error_messages,
        "timezone": "Include an explicit UTC offset, such as Z or +00:00.",
    }

    def to_internal_value(self, value):
        parsed = value if isinstance(value, datetime) else None
        if isinstance(value, str):
            parsed = parse_datetime(value)
        if parsed is not None and timezone.is_naive(parsed):
            self.fail("timezone")
        return super().to_internal_value(value)


class ScheduledEventSerializer(serializers.ModelSerializer):
    phone_number_id = serializers.PrimaryKeyRelatedField(
        source="phone_number",
        queryset=PhoneNumber.objects.none(),
    )
    scheduled_for = ExplicitTimezoneDateTimeField()

    class Meta:
        model = ScheduledEvent
        fields = [
            "id",
            "phone_number_id",
            "zip_code",
            "scheduled_for",
            "channel",
            "status",
            "is_demo",
            "processing_started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "is_demo",
            "processing_started_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            self.fields["phone_number_id"].queryset = PhoneNumber.objects.filter(
                user=request.user,
                verified_at__isnull=False,
            )

    def validate_scheduled_for(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("The scheduled time must be in the future.")
        return value

    def create(self, validated_data):
        phone_number = validated_data.pop("phone_number")
        try:
            return create_scheduled_event(
                user=self.context["request"].user,
                phone_number_id=phone_number.id,
                **validated_data,
            )
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            raise serializers.ValidationError(detail) from None
