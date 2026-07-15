from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class DeliveryAttempt(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        SUBMITTED = "submitted", "Submitted"
        FAILED = "failed", "Failed"
        SUPPRESSED = "suppressed", "Suppressed"

    TERMINAL_STATUSES = {Status.SUBMITTED, Status.FAILED, Status.SUPPRESSED}

    class ProviderStatus(models.TextChoices):
        QUEUED = "queued", "Queued"
        INITIATED = "initiated", "Initiated"
        RINGING = "ringing", "Ringing"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        BUSY = "busy", "Busy"
        NO_ANSWER = "no_answer", "No answer"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    TERMINAL_PROVIDER_STATUSES = {
        ProviderStatus.COMPLETED,
        ProviderStatus.BUSY,
        ProviderStatus.NO_ANSWER,
        ProviderStatus.FAILED,
        ProviderStatus.CANCELED,
    }

    event = models.ForeignKey(
        "scheduling.ScheduledEvent",
        on_delete=models.CASCADE,
        related_name="delivery_attempts",
    )
    attempt_number = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PROCESSING,
    )
    rendered_message = models.TextField(blank=True)
    weather_snapshot = models.JSONField(blank=True, default=dict)
    provider_sid = models.CharField(max_length=64, blank=True, null=True, unique=True)
    provider_status = models.CharField(
        max_length=12,
        choices=ProviderStatus.choices,
        blank=True,
    )
    provider_status_sequence = models.PositiveIntegerField(blank=True, null=True)
    provider_status_updated_at = models.DateTimeField(blank=True, null=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["event", "attempt_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "attempt_number"],
                name="unique_delivery_attempt_number",
            )
        ]

    def __str__(self):
        return f"Attempt {self.attempt_number} for event {self.event_id}"

    def transition_to(self, new_status, *, at=None):
        if self.status != self.Status.PROCESSING or new_status not in self.TERMINAL_STATUSES:
            raise ValidationError(
                {"status": f"Cannot transition from {self.status} to {new_status}."}
            )

        self.status = new_status
        self.completed_at = at or timezone.now()

    def apply_provider_status(self, new_status, *, sequence_number, at=None):
        if new_status not in self.ProviderStatus.values:
            raise ValidationError({"provider_status": "Unknown provider status."})
        if sequence_number < 0:
            raise ValidationError(
                {"provider_status_sequence": "Sequence number cannot be negative."}
            )
        if self.provider_status in self.TERMINAL_PROVIDER_STATUSES:
            return False
        if (
            self.provider_status_sequence is not None
            and sequence_number <= self.provider_status_sequence
        ):
            return False

        self.provider_status = new_status
        self.provider_status_sequence = sequence_number
        self.provider_status_updated_at = at or timezone.now()
        return True
