from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


zip_code_validator = RegexValidator(
    regex=r"^\d{5}$",
    message="Enter a five-digit US ZIP code.",
)


class ScheduledEvent(models.Model):
    class Channel(models.TextChoices):
        SMS = "sms", "SMS"
        VOICE = "voice", "Voice"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        PROCESSING = "processing", "Processing"
        SUBMITTED = "submitted", "Submitted"
        FAILED = "failed", "Failed"
        SUPPRESSED = "suppressed", "Suppressed"
        CANCELLED = "cancelled", "Cancelled"

    ALLOWED_TRANSITIONS = {
        Status.SCHEDULED: {Status.PROCESSING, Status.CANCELLED},
        Status.PROCESSING: {Status.SUBMITTED, Status.FAILED, Status.SUPPRESSED},
    }

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="scheduled_events",
    )
    phone_number = models.ForeignKey(
        "accounts.PhoneNumber",
        on_delete=models.PROTECT,
        related_name="scheduled_events",
    )
    zip_code = models.CharField(max_length=5, validators=[zip_code_validator])
    scheduled_for = models.DateTimeField()
    channel = models.CharField(max_length=10, choices=Channel.choices)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )
    is_demo = models.BooleanField(default=True)
    processing_started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_for"]
        indexes = [
            models.Index(fields=["status", "scheduled_for"]),
            models.Index(fields=["user", "scheduled_for"]),
        ]

    def __str__(self):
        return f"{self.get_channel_display()} for {self.user} at {self.scheduled_for}"

    def clean(self):
        super().clean()
        errors = {}

        if self.phone_number_id and self.user_id:
            if self.phone_number.user_id != self.user_id:
                errors["phone_number"] = "The phone number must belong to the event user."
            elif not self.phone_number.is_verified:
                errors["phone_number"] = "The phone number must be verified."

        if (
            self._state.adding
            and self.scheduled_for
            and self.scheduled_for <= timezone.now()
        ):
            errors["scheduled_for"] = "The scheduled time must be in the future."

        if errors:
            raise ValidationError(errors)

    def transition_to(self, new_status, *, at=None):
        allowed = self.ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValidationError(
                {"status": f"Cannot transition from {self.status} to {new_status}."}
            )

        transition_time = at or timezone.now()
        self.status = new_status

        if new_status == self.Status.PROCESSING:
            self.processing_started_at = transition_time
        elif new_status in {
            self.Status.SUBMITTED,
            self.Status.FAILED,
            self.Status.SUPPRESSED,
            self.Status.CANCELLED,
        }:
            self.completed_at = transition_time
