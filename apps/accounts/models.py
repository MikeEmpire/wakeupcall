from django.contrib.auth.models import AbstractUser  
from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models


class User(AbstractUser):
    pass


e164_validator = RegexValidator(
    regex=r"^\+[1-9]\d{1,14}$",
    message="Enter a phone number in E.164 format, such as +14155552671.",
)


class PhoneNumber(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="phone_numbers",
    )
    number = models.CharField(max_length=16, unique=True, validators=[e164_validator])
    verified_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return self.number

    @property
    def is_verified(self):
        return self.verified_at is not None
