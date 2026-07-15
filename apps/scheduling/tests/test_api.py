import base64
from datetime import UTC, datetime, timedelta, timezone as datetime_timezone

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import PhoneNumber
from apps.scheduling.models import ScheduledEvent


@pytest.fixture
def api_user(db):
    return get_user_model().objects.create_user(
        username="api-user",
        password="safe-test-password",
    )


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user(username="other-api-user")


@pytest.fixture
def verified_phone(api_user):
    return PhoneNumber.objects.create(
        user=api_user,
        number="+14155552671",
        verified_at=timezone.now(),
    )


@pytest.fixture
def unverified_phone(api_user):
    return PhoneNumber.objects.create(
        user=api_user,
        number="+14155550000",
    )


@pytest.fixture
def other_phone(other_user):
    return PhoneNumber.objects.create(
        user=other_user,
        number="+14155559999",
        verified_at=timezone.now(),
    )


@pytest.fixture
def api_client(api_user):
    client = APIClient()
    client.force_authenticate(api_user)
    return client


def create_payload(phone, **overrides):
    values = {
        "phone_number_id": phone.id,
        "zip_code": "94107",
        "scheduled_for": (timezone.now() + timedelta(hours=1)).isoformat(),
        "channel": ScheduledEvent.Channel.SMS,
    }
    values.update(overrides)
    return values


def create_event(user, phone, **overrides):
    values = {
        "user": user,
        "phone_number": phone,
        "zip_code": "94107",
        "scheduled_for": timezone.now() + timedelta(hours=1),
        "channel": ScheduledEvent.Channel.SMS,
        "is_demo": True,
    }
    values.update(overrides)
    return ScheduledEvent.objects.create(**values)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "url_name", "kwargs"),
    [
        ("get", "scheduling:event-list", {}),
        ("post", "scheduling:event-list", {}),
        ("get", "scheduling:event-detail", {"event_id": 1}),
        ("post", "scheduling:event-cancel", {"event_id": 1}),
    ],
)
def test_event_api_requires_authentication(method, url_name, kwargs):
    client = APIClient()

    response = getattr(client, method)(reverse(url_name, kwargs=kwargs), {})

    assert response.status_code == 401


@pytest.mark.django_db
def test_basic_authentication_can_access_event_list(api_user):
    client = APIClient()
    credentials = base64.b64encode(
        b"api-user:safe-test-password"
    ).decode("ascii")
    client.credentials(HTTP_AUTHORIZATION=f"Basic {credentials}")

    response = client.get(reverse("scheduling:event-list"))

    assert response.status_code == 200
    assert response.data["count"] == 0


@pytest.mark.django_db
def test_create_event_is_user_owned_demo_and_returns_no_phone_number(
    api_client, api_user, verified_phone
):
    response = api_client.post(
        reverse("scheduling:event-list"),
        create_payload(
            verified_phone,
            is_demo=False,
            status=ScheduledEvent.Status.SUBMITTED,
        ),
        format="json",
    )

    assert response.status_code == 201
    event = ScheduledEvent.objects.get()
    assert event.user == api_user
    assert event.phone_number == verified_phone
    assert event.is_demo is True
    assert event.status == ScheduledEvent.Status.SCHEDULED
    assert response.data["phone_number_id"] == verified_phone.id
    assert verified_phone.number not in str(response.data)


@pytest.mark.django_db
def test_create_normalizes_explicit_offset_to_utc(
    api_client, api_user, verified_phone
):
    scheduled_utc = datetime.now(UTC) + timedelta(hours=2)
    supplied = scheduled_utc.astimezone(datetime_timezone(timedelta(hours=2)))

    response = api_client.post(
        reverse("scheduling:event-list"),
        create_payload(verified_phone, scheduled_for=supplied.isoformat()),
        format="json",
    )

    event = ScheduledEvent.objects.get(user=api_user)
    assert response.status_code == 201
    assert event.scheduled_for == supplied
    assert response.data["scheduled_for"].endswith("Z")


@pytest.mark.django_db
def test_create_rejects_naive_datetime(api_client, verified_phone):
    naive_future = (datetime.now() + timedelta(hours=1)).isoformat()

    response = api_client.post(
        reverse("scheduling:event-list"),
        create_payload(verified_phone, scheduled_for=naive_future),
        format="json",
    )

    assert response.status_code == 400
    assert "explicit UTC offset" in str(response.data["scheduled_for"])
    assert ScheduledEvent.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"zip_code": "9410"}, "zip_code"),
        ({"channel": "email"}, "channel"),
        ({"scheduled_for": "2000-01-01T00:00:00Z"}, "scheduled_for"),
    ],
)
def test_create_rejects_invalid_domain_input(
    api_client, verified_phone, overrides, field
):
    response = api_client.post(
        reverse("scheduling:event-list"),
        create_payload(verified_phone, **overrides),
        format="json",
    )

    assert response.status_code == 400
    assert field in response.data


@pytest.mark.django_db
@pytest.mark.parametrize("phone_fixture", ["unverified_phone", "other_phone"])
def test_create_rejects_unverified_or_other_users_phone(
    request,
    api_client,
    phone_fixture,
):
    phone = request.getfixturevalue(phone_fixture)

    response = api_client.post(
        reverse("scheduling:event-list"),
        create_payload(phone),
        format="json",
    )

    assert response.status_code == 400
    assert "phone_number_id" in response.data
    assert ScheduledEvent.objects.count() == 0


@pytest.mark.django_db
def test_list_is_paginated_ordered_and_user_scoped(
    api_client, api_user, verified_phone, other_user, other_phone
):
    later = create_event(
        api_user,
        verified_phone,
        scheduled_for=timezone.now() + timedelta(hours=2),
    )
    earlier = create_event(
        api_user,
        verified_phone,
        scheduled_for=timezone.now() + timedelta(hours=1),
    )
    create_event(other_user, other_phone)

    response = api_client.get(reverse("scheduling:event-list"))

    assert response.status_code == 200
    assert response.data["count"] == 2
    assert [item["id"] for item in response.data["results"]] == [
        earlier.id,
        later.id,
    ]
    assert verified_phone.number not in str(response.data)
    assert other_phone.number not in str(response.data)


@pytest.mark.django_db
def test_retrieve_returns_owned_event(api_client, api_user, verified_phone):
    event = create_event(api_user, verified_phone)

    response = api_client.get(
        reverse("scheduling:event-detail", kwargs={"event_id": event.id})
    )

    assert response.status_code == 200
    assert response.data["id"] == event.id


@pytest.mark.django_db
def test_retrieve_and_cancel_hide_other_users_event(
    api_client, other_user, other_phone
):
    event = create_event(other_user, other_phone)

    retrieve = api_client.get(
        reverse("scheduling:event-detail", kwargs={"event_id": event.id})
    )
    cancel = api_client.post(
        reverse("scheduling:event-cancel", kwargs={"event_id": event.id})
    )

    event.refresh_from_db()
    assert retrieve.status_code == 404
    assert cancel.status_code == 404
    assert event.status == ScheduledEvent.Status.SCHEDULED


@pytest.mark.django_db
def test_cancel_owned_scheduled_event(api_client, api_user, verified_phone):
    event = create_event(api_user, verified_phone)

    response = api_client.post(
        reverse("scheduling:event-cancel", kwargs={"event_id": event.id})
    )

    event.refresh_from_db()
    assert response.status_code == 200
    assert response.data["status"] == ScheduledEvent.Status.CANCELLED
    assert event.status == ScheduledEvent.Status.CANCELLED
    assert event.completed_at is not None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "event_status",
    [
        ScheduledEvent.Status.PROCESSING,
        ScheduledEvent.Status.SUBMITTED,
        ScheduledEvent.Status.FAILED,
        ScheduledEvent.Status.SUPPRESSED,
        ScheduledEvent.Status.CANCELLED,
    ],
)
def test_cancel_rejects_non_scheduled_event_with_conflict(
    api_client,
    api_user,
    verified_phone,
    event_status,
):
    event = create_event(api_user, verified_phone, status=event_status)

    response = api_client.post(
        reverse("scheduling:event-cancel", kwargs={"event_id": event.id})
    )

    assert response.status_code == 409
    assert "status" in response.data


@pytest.mark.django_db
def test_event_api_exposes_no_mutating_detail_methods(
    api_client, api_user, verified_phone
):
    event = create_event(api_user, verified_phone)
    detail_url = reverse("scheduling:event-detail", kwargs={"event_id": event.id})

    assert api_client.put(detail_url, {}, format="json").status_code == 405
    assert api_client.patch(detail_url, {}, format="json").status_code == 405
    assert api_client.delete(detail_url).status_code == 405
