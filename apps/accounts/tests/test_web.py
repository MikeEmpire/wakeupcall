from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse

from apps.accounts.models import PhoneNumber
from apps.accounts.verification import VerificationResult, VerificationStatus


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def web_user(db):
    return get_user_model().objects.create_user(
        username="web-user",
        password="safe-test-password",
    )


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user(username="other-web-user")


@pytest.fixture
def logged_in_client(client, web_user):
    client.force_login(web_user)
    return client


@pytest.fixture
def phone(web_user):
    return PhoneNumber.objects.create(user=web_user, number="+14155552671")


def install_gateway(monkeypatch, gateway):
    factory = Mock(return_value=gateway)
    monkeypatch.setattr(
        "apps.accounts.web_views.get_phone_verification_gateway",
        factory,
    )
    return factory


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "url_name", "kwargs"),
    [
        ("get", "accounts_web:phone-list", {}),
        ("get", "accounts_web:phone-enroll", {}),
        ("get", "accounts_web:phone-verify", {"phone_number_id": 1}),
        (
            "post",
            "accounts_web:phone-verification-start",
            {"phone_number_id": 1},
        ),
    ],
)
def test_phone_pages_require_login(client, method, url_name, kwargs):
    response = getattr(client, method)(reverse(url_name, kwargs=kwargs))

    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))


@pytest.mark.django_db
def test_login_and_logout_work_for_existing_user(client, web_user):
    login_response = client.post(
        reverse("login"),
        {"username": web_user.username, "password": "safe-test-password"},
    )

    assert login_response.status_code == 302
    assert login_response.url == reverse("scheduling_web:event-list")

    logout_response = client.post(reverse("logout"))
    assert logout_response.status_code == 302
    assert logout_response.url == reverse("login")


@pytest.mark.django_db
def test_phone_list_is_owner_scoped_masked_and_hides_admin_for_ordinary_user(
    logged_in_client,
    web_user,
    other_user,
):
    owned = PhoneNumber.objects.create(user=web_user, number="+14155552671")
    other = PhoneNumber.objects.create(user=other_user, number="+14155559999")

    response = logged_in_client.get(reverse("accounts_web:phone-list"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "*******2671" in content
    assert owned.number not in content
    assert other.number not in content
    assert "Staff admin" not in content


@pytest.mark.django_db
def test_staff_user_sees_admin_navigation(client):
    staff = get_user_model().objects.create_user(username="staff", is_staff=True)
    client.force_login(staff)

    response = client.get(reverse("accounts_web:phone-list"))

    assert response.status_code == 200
    assert "Staff admin" in response.content.decode()


@pytest.mark.django_db
def test_phone_enrollment_creates_owned_unverified_phone_and_masks_redirect_page(
    logged_in_client,
    web_user,
):
    number = "+14155552671"

    response = logged_in_client.post(
        reverse("accounts_web:phone-enroll"),
        {"number": number},
        follow=True,
    )

    phone = PhoneNumber.objects.get()
    content = response.content.decode()
    assert response.status_code == 200
    assert phone.user == web_user
    assert phone.verified_at is None
    assert "Verify ********2671" in content
    assert number not in content


@pytest.mark.django_db
def test_phone_enrollment_returns_safe_errors(
    logged_in_client,
    other_user,
):
    number = "+14155552671"
    PhoneNumber.objects.create(user=other_user, number=number)

    duplicate = logged_in_client.post(
        reverse("accounts_web:phone-enroll"),
        {"number": number},
    )
    invalid = logged_in_client.post(
        reverse("accounts_web:phone-enroll"),
        {"number": "415-555-2671"},
    )

    assert duplicate.status_code == 400
    assert "cannot be enrolled" in duplicate.content.decode()
    assert invalid.status_code == 400
    assert "E.164" in invalid.content.decode()


@pytest.mark.django_db
def test_verification_start_uses_gateway_and_never_exposes_provider_data(
    logged_in_client,
    phone,
    monkeypatch,
):
    gateway = Mock()
    gateway.start_verification.return_value = VerificationResult(
        status=VerificationStatus.PENDING,
        provider_sid="VE-secret",
    )
    install_gateway(monkeypatch, gateway)

    response = logged_in_client.post(
        reverse(
            "accounts_web:phone-verification-start",
            kwargs={"phone_number_id": phone.id},
        ),
        follow=True,
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Verification code requested" in content
    assert phone.number not in content
    assert "VE-secret" not in content
    gateway.start_verification.assert_called_once_with(phone.number)


@pytest.mark.django_db
def test_approved_check_verifies_phone_and_rejected_check_does_not(
    logged_in_client,
    phone,
    monkeypatch,
):
    gateway = Mock()
    gateway.check_verification.return_value = VerificationResult(
        status=VerificationStatus.REJECTED
    )
    install_gateway(monkeypatch, gateway)
    url = reverse(
        "accounts_web:phone-verify",
        kwargs={"phone_number_id": phone.id},
    )

    rejected = logged_in_client.post(url, {"code": "123456"})
    phone.refresh_from_db()
    assert rejected.status_code == 400
    assert "not approved" in rejected.content.decode()
    assert phone.verified_at is None

    gateway.check_verification.return_value = VerificationResult(
        status=VerificationStatus.APPROVED
    )
    approved = logged_in_client.post(url, {"code": "123456"}, follow=True)
    phone.refresh_from_db()
    assert approved.status_code == 200
    assert "Phone verified" in approved.content.decode()
    assert phone.verified_at is not None


@pytest.mark.django_db
def test_phone_verification_hides_other_users_phone(
    logged_in_client,
    other_user,
    monkeypatch,
):
    other_phone = PhoneNumber.objects.create(
        user=other_user,
        number="+14155559999",
    )
    factory = install_gateway(monkeypatch, Mock())

    page = logged_in_client.get(
        reverse(
            "accounts_web:phone-verify",
            kwargs={"phone_number_id": other_phone.id},
        )
    )
    start = logged_in_client.post(
        reverse(
            "accounts_web:phone-verification-start",
            kwargs={"phone_number_id": other_phone.id},
        )
    )

    assert page.status_code == 404
    assert start.status_code == 404
    factory.assert_not_called()


@pytest.mark.django_db
def test_phone_mutations_require_csrf(client, web_user, phone):
    csrf_client = client.__class__(enforce_csrf_checks=True)
    csrf_client.force_login(web_user)

    enroll = csrf_client.post(
        reverse("accounts_web:phone-enroll"),
        {"number": "+14155550000"},
    )
    start = csrf_client.post(
        reverse(
            "accounts_web:phone-verification-start",
            kwargs={"phone_number_id": phone.id},
        )
    )

    assert enroll.status_code == 403
    assert start.status_code == 403


@pytest.mark.django_db
def test_web_verification_start_uses_shared_throttle_scope(
    logged_in_client,
    phone,
    monkeypatch,
):
    gateway = Mock()
    gateway.start_verification.return_value = VerificationResult(
        status=VerificationStatus.PENDING
    )
    install_gateway(monkeypatch, gateway)
    url = reverse(
        "accounts_web:phone-verification-start",
        kwargs={"phone_number_id": phone.id},
    )

    responses = [logged_in_client.post(url, follow=True) for _ in range(4)]

    assert "Too many verification starts" in responses[-1].content.decode()
    assert gateway.start_verification.call_count == 3
