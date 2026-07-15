from django.test import override_settings
from django.urls import reverse


def test_health_endpoint(client):
    response = client.get(reverse("health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@override_settings(
    ALLOWED_HOSTS=["wakeup.example.test"],
    SECURE_SSL_REDIRECT=True,
)
def test_health_endpoint_accepts_private_alb_health_check(client):
    response = client.get(
        reverse("health"),
        headers={
            "host": "10.42.10.25:8000",
            "user-agent": "ELB-HealthChecker/2.0",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
