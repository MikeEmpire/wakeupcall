import base64
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import PhoneNumber
from apps.accounts.verification import VerificationResult, VerificationStatus
from apps.accounts.verification_exceptions import (
    VerificationExpired,
    VerificationProviderUnavailable,
    VerificationRateLimited,
)


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_user(db):
    return get_user_model().objects.create_user(
        username="phone-api-user",
        password="safe-test-password",
    )


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user(username="other-phone-api-user")


@pytest.fixture
def api_client(api_user):
    client = APIClient()
    client.force_authenticate(api_user)
    return client


@pytest.fixture
def phone_number(api_user):
    return PhoneNumber.objects.create(user=api_user, number="+14155552671")


def install_gateway(monkeypatch, gateway):
    factory = Mock(return_value=gateway)
    monkeypatch.setattr(
        "apps.accounts.views.get_phone_verification_gateway",
        factory,
    )
    return factory


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "url_name", "kwargs"),
    [
        ("get", "accounts:phone-list", {}),
        ("post", "accounts:phone-list", {}),
        ("post", "accounts:phone-verification-start", {"phone_number_id": 1}),
        ("post", "accounts:phone-verification-check", {"phone_number_id": 1}),
    ],
)
def test_phone_api_requires_authentication(method, url_name, kwargs):
    client = APIClient()

    response = getattr(client, method)(reverse(url_name, kwargs=kwargs), {})

    assert response.status_code == 401


@pytest.mark.django_db
def test_basic_authentication_can_list_phones(api_user):
    client = APIClient()
    credentials = base64.b64encode(
        b"phone-api-user:safe-test-password"
    ).decode("ascii")
    client.credentials(HTTP_AUTHORIZATION=f"Basic {credentials}")

    response = client.get(reverse("accounts:phone-list"))

    assert response.status_code == 200
    assert response.data["count"] == 0


@pytest.mark.django_db
def test_create_phone_is_owned_unverified_and_returns_only_masked_number(
    api_client,
    api_user,
):
    number = "+14155552671"

    response = api_client.post(
        reverse("accounts:phone-list"),
        {"number": number},
        format="json",
    )

    phone = PhoneNumber.objects.get()
    assert response.status_code == 201
    assert phone.user == api_user
    assert phone.verified_at is None
    assert response.data["masked_number"].endswith("2671")
    assert number not in str(response.data)
    assert "number" not in response.data


@pytest.mark.django_db
def test_list_is_paginated_owner_scoped_and_masked(
    api_client,
    api_user,
    other_user,
):
    owned = PhoneNumber.objects.create(user=api_user, number="+14155552671")
    other = PhoneNumber.objects.create(user=other_user, number="+14155559999")

    response = api_client.get(reverse("accounts:phone-list"))

    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == owned.id
    assert owned.number not in str(response.data)
    assert other.number not in str(response.data)


@pytest.mark.django_db
@pytest.mark.parametrize("number", ["415-555-2671", "+0123", "", None])
def test_create_rejects_invalid_phone_number(api_client, number):
    response = api_client.post(
        reverse("accounts:phone-list"),
        {"number": number},
        format="json",
    )

    assert response.status_code == 400
    assert "number" in response.data
    assert PhoneNumber.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("existing_owner", ["api_user", "other_user"])
def test_create_rejects_duplicate_number_with_same_safe_error(
    request,
    api_client,
    existing_owner,
):
    owner = request.getfixturevalue(existing_owner)
    number = "+14155552671"
    PhoneNumber.objects.create(user=owner, number=number)

    response = api_client.post(
        reverse("accounts:phone-list"),
        {"number": number},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["number"] == ["This phone number cannot be enrolled."]
    assert number not in str(response.data)


@pytest.mark.django_db
def test_start_verification_uses_gateway_and_omits_provider_data(
    api_client,
    phone_number,
    monkeypatch,
):
    gateway = Mock()
    gateway.start_verification.return_value = VerificationResult(
        status=VerificationStatus.PENDING,
        provider_sid="VE-secret-provider-id",
    )
    install_gateway(monkeypatch, gateway)

    response = api_client.post(
        reverse(
            "accounts:phone-verification-start",
            kwargs={"phone_number_id": phone_number.id},
        )
    )

    assert response.status_code == 200
    assert response.data["verification_status"] == VerificationStatus.PENDING
    assert phone_number.number not in str(response.data)
    assert "VE-secret-provider-id" not in str(response.data)
    gateway.start_verification.assert_called_once_with(phone_number.number)


@pytest.mark.django_db
def test_start_verification_rejects_already_verified_phone_without_gateway(
    api_client,
    phone_number,
    monkeypatch,
):
    phone_number.verified_at = timezone.now()
    phone_number.save(update_fields=["verified_at"])
    factory = install_gateway(monkeypatch, Mock())

    response = api_client.post(
        reverse(
            "accounts:phone-verification-start",
            kwargs={"phone_number_id": phone_number.id},
        )
    )

    assert response.status_code == 409
    factory.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("code", [None, "123", "12345678901", "12A4"])
def test_check_rejects_invalid_code_before_gateway(
    api_client,
    phone_number,
    monkeypatch,
    code,
):
    factory = install_gateway(monkeypatch, Mock())
    payload = {} if code is None else {"code": code}

    response = api_client.post(
        reverse(
            "accounts:phone-verification-check",
            kwargs={"phone_number_id": phone_number.id},
        ),
        payload,
        format="json",
    )

    assert response.status_code == 400
    assert "code" in response.data
    factory.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("provider_status", "expected_verified"),
    [
        (VerificationStatus.REJECTED, False),
        (VerificationStatus.APPROVED, True),
    ],
)
def test_check_returns_normalized_status_and_updates_only_after_approval(
    api_client,
    phone_number,
    monkeypatch,
    provider_status,
    expected_verified,
):
    code = "123456"
    gateway = Mock()
    gateway.check_verification.return_value = VerificationResult(
        status=provider_status,
        provider_sid="VE-secret-provider-id",
    )
    install_gateway(monkeypatch, gateway)

    response = api_client.post(
        reverse(
            "accounts:phone-verification-check",
            kwargs={"phone_number_id": phone_number.id},
        ),
        {"code": code},
        format="json",
    )

    phone_number.refresh_from_db()
    assert response.status_code == 200
    assert response.data["verification_status"] == provider_status
    assert phone_number.is_verified is expected_verified
    assert code not in str(response.data)
    assert "VE-secret-provider-id" not in str(response.data)


@pytest.mark.django_db
def test_check_is_idempotent_for_verified_phone_without_gateway(
    api_client,
    phone_number,
    monkeypatch,
):
    phone_number.verified_at = timezone.now()
    phone_number.save(update_fields=["verified_at"])
    factory = install_gateway(monkeypatch, Mock())

    response = api_client.post(
        reverse(
            "accounts:phone-verification-check",
            kwargs={"phone_number_id": phone_number.id},
        ),
        {"code": "123456"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["verification_status"] == VerificationStatus.APPROVED
    factory.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("url_name", "payload"),
    [
        ("accounts:phone-verification-start", {}),
        ("accounts:phone-verification-check", {"code": "123456"}),
    ],
)
def test_verification_actions_hide_missing_and_other_users_phone(
    api_client,
    other_user,
    monkeypatch,
    url_name,
    payload,
):
    other_phone = PhoneNumber.objects.create(
        user=other_user,
        number="+14155559999",
    )
    factory = install_gateway(monkeypatch, Mock())

    other_response = api_client.post(
        reverse(url_name, kwargs={"phone_number_id": other_phone.id}),
        payload,
        format="json",
    )
    missing_response = api_client.post(
        reverse(url_name, kwargs={"phone_number_id": other_phone.id + 1000}),
        payload,
        format="json",
    )

    assert other_response.status_code == 404
    assert missing_response.status_code == 404
    factory.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("provider_error", "expected_status"),
    [
        (VerificationExpired("provider detail"), 400),
        (VerificationRateLimited("provider detail"), 429),
        (VerificationProviderUnavailable("provider detail"), 503),
    ],
)
def test_check_maps_provider_errors_without_exposing_details(
    api_client,
    phone_number,
    monkeypatch,
    provider_error,
    expected_status,
):
    gateway = Mock()
    gateway.check_verification.side_effect = provider_error
    install_gateway(monkeypatch, gateway)

    response = api_client.post(
        reverse(
            "accounts:phone-verification-check",
            kwargs={"phone_number_id": phone_number.id},
        ),
        {"code": "123456"},
        format="json",
    )

    assert response.status_code == expected_status
    assert "provider detail" not in str(response.data)


@pytest.mark.django_db
def test_start_verification_is_throttled_per_authenticated_user(
    api_client,
    phone_number,
    monkeypatch,
):
    gateway = Mock()
    gateway.start_verification.return_value = VerificationResult(
        status=VerificationStatus.PENDING
    )
    install_gateway(monkeypatch, gateway)
    url = reverse(
        "accounts:phone-verification-start",
        kwargs={"phone_number_id": phone_number.id},
    )

    responses = [api_client.post(url) for _ in range(4)]

    assert [response.status_code for response in responses] == [200, 200, 200, 429]
    assert gateway.start_verification.call_count == 3


@pytest.mark.django_db
def test_verification_checks_are_throttled_per_authenticated_user(
    api_client,
    phone_number,
    monkeypatch,
):
    gateway = Mock()
    gateway.check_verification.return_value = VerificationResult(
        status=VerificationStatus.REJECTED
    )
    install_gateway(monkeypatch, gateway)
    url = reverse(
        "accounts:phone-verification-check",
        kwargs={"phone_number_id": phone_number.id},
    )

    responses = [
        api_client.post(url, {"code": "123456"}, format="json")
        for _ in range(11)
    ]

    assert [response.status_code for response in responses] == [200] * 10 + [429]
    assert gateway.check_verification.call_count == 10
