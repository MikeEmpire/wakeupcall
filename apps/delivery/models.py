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
