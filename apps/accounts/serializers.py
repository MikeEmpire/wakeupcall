from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.accounts.models import PhoneNumber, e164_validator
from apps.accounts.services import create_phone_number


def mask_phone_number(number: str) -> str:
    visible_digits = 4
    if len(number) <= visible_digits:
        return "*" * len(number)
    return f"{'*' * (len(number) - visible_digits)}{number[-visible_digits:]}"


class PhoneNumberSerializer(serializers.ModelSerializer):
    number = serializers.CharField(
        write_only=True,
        validators=[e164_validator],
    )
    masked_number = serializers.SerializerMethodField()
    is_verified = serializers.BooleanField(read_only=True)

    class Meta:
        model = PhoneNumber
        fields = [
            "id",
            "number",
            "masked_number",
            "is_verified",
            "verified_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "masked_number",
            "is_verified",
            "verified_at",
            "created_at",
            "updated_at",
        ]

    def get_masked_number(self, obj):
        return mask_phone_number(obj.number)

    def create(self, validated_data):
        try:
            return create_phone_number(
                user=self.context["request"].user,
                number=validated_data["number"],
            )
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            raise serializers.ValidationError(detail) from None


class VerificationCheckSerializer(serializers.Serializer):
    code = serializers.RegexField(
        regex=r"^\d{4,10}$",
        write_only=True,
        error_messages={
            "invalid": "The verification code must contain 4 to 10 digits."
        },
    )
