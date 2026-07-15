from datetime import UTC, datetime, timedelta, timezone as datetime_timezone

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import PhoneNumber
from apps.delivery.models import DeliveryAttempt
from apps.scheduling.models import ScheduledEvent


@pytest.fixture
def web_user(db):
    return get_user_model().objects.create_user(username="event-web-user")


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user(username="other-event-web-user")


@pytest.fixture
def logged_in_client(client, web_user):
    client.force_login(web_user)
    return client


@pytest.fixture
def verified_phone(web_user):
    return PhoneNumber.objects.create(
        user=web_user,
        number="+14155552671",
        verified_at=timezone.now(),
    )


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
        ("get", "scheduling_web:event-list", {}),
        ("get", "scheduling_web:event-create", {}),
        ("get", "scheduling_web:event-detail", {"event_id": 1}),
        ("post", "scheduling_web:event-reschedule", {"event_id": 1}),
        ("post", "scheduling_web:event-channel", {"event_id": 1}),
        ("post", "scheduling_web:event-cancel", {"event_id": 1}),
    ],
)
def test_event_pages_require_login(client, method, url_name, kwargs):
    response = getattr(client, method)(reverse(url_name, kwargs=kwargs))

    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))


@pytest.mark.django_db
def test_event_list_is_owner_scoped_and_masks_phone(
    logged_in_client,
    web_user,
    verified_phone,
    other_user,
):
    owned = create_event(web_user, verified_phone)
    other_phone = PhoneNumber.objects.create(
        user=other_user,
        number="+14155559999",
        verified_at=timezone.now(),
    )
    other = create_event(other_user, other_phone)

    response = logged_in_client.get(reverse("scheduling_web:event-list"))
    content = response.content.decode()

    assert response.status_code == 200
    assert reverse("scheduling_web:event-detail", args=[owned.id]) in content
    assert reverse("scheduling_web:event-detail", args=[other.id]) not in content
    assert verified_phone.number not in content
    assert other_phone.number not in content


@pytest.mark.django_db
def test_create_form_lists_only_owned_verified_phones(
    logged_in_client,
    web_user,
    verified_phone,
    other_user,
):
    unverified = PhoneNumber.objects.create(
        user=web_user,
        number="+14155550000",
    )
    other_phone = PhoneNumber.objects.create(
        user=other_user,
        number="+14155559999",
        verified_at=timezone.now(),
    )

    response = logged_in_client.get(reverse("scheduling_web:event-create"))
    choices = list(response.context["form"].fields["phone_number"].queryset)
    content = response.content.decode()

    assert response.status_code == 200
    assert choices == [verified_phone]
    assert unverified not in choices
    assert other_phone not in choices
    assert verified_phone.number not in content
    assert "********2671" in content


@pytest.mark.django_db
def test_create_event_normalizes_offset_and_always_creates_demo(
    logged_in_client,
    web_user,
    verified_phone,
):
    supplied = (datetime.now(UTC) + timedelta(hours=2)).astimezone(
        datetime_timezone(timedelta(hours=3))
    )

    response = logged_in_client.post(
        reverse("scheduling_web:event-create"),
        {
            "phone_number": verified_phone.id,
            "zip_code": "94107",
            "scheduled_for": supplied.isoformat(),
            "channel": ScheduledEvent.Channel.VOICE,
        },
    )

    event = ScheduledEvent.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("scheduling_web:event-detail", args=[event.id])
    assert event.user == web_user
    assert event.scheduled_for == supplied
    assert event.scheduled_for.utcoffset() == timedelta(0)
    assert event.is_demo is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("scheduled_for", "message"),
    [
        ("not-a-date", "valid ISO 8601"),
        ("2030-01-01T12:00:00", "explicit UTC offset"),
        ("2000-01-01T00:00:00Z", "must be in the future"),
    ],
)
def test_create_rejects_invalid_time(
    logged_in_client,
    verified_phone,
    scheduled_for,
    message,
):
    response = logged_in_client.post(
        reverse("scheduling_web:event-create"),
        {
            "phone_number": verified_phone.id,
            "zip_code": "94107",
            "scheduled_for": scheduled_for,
            "channel": ScheduledEvent.Channel.SMS,
        },
    )

    assert response.status_code == 400
    assert message in response.content.decode()
    assert ScheduledEvent.objects.count() == 0


@pytest.mark.django_db
def test_event_detail_hides_other_users_event_and_attempt_internals(
    logged_in_client,
    web_user,
    verified_phone,
    other_user,
):
    event = create_event(web_user, verified_phone)
    DeliveryAttempt.objects.create(
        event=event,
        attempt_number=1,
        status="submitted",
        rendered_message="private rendered weather announcement",
        provider_sid="SMsecret",
        started_at=timezone.now(),
        completed_at=timezone.now(),
    )
    other_phone = PhoneNumber.objects.create(
        user=other_user,
        number="+14155559999",
        verified_at=timezone.now(),
    )
    other_event = create_event(other_user, other_phone)

    owned = logged_in_client.get(
        reverse("scheduling_web:event-detail", args=[event.id])
    )
    hidden = logged_in_client.get(
        reverse("scheduling_web:event-detail", args=[other_event.id])
    )
    content = owned.content.decode()

    assert owned.status_code == 200
    assert hidden.status_code == 404
    assert "private rendered" not in content
    assert "SMsecret" not in content
    assert verified_phone.number not in content


@pytest.mark.django_db
def test_pending_event_controls_use_services_and_preserve_other_fields(
    logged_in_client,
    web_user,
    verified_phone,
):
    event = create_event(web_user, verified_phone)
    original = (event.phone_number_id, event.zip_code, event.is_demo)
    future = timezone.now() + timedelta(hours=3)

    reschedule = logged_in_client.post(
        reverse("scheduling_web:event-reschedule", args=[event.id]),
        {"scheduled_for": future.isoformat()},
    )
    channel = logged_in_client.post(
        reverse("scheduling_web:event-channel", args=[event.id]),
        {"channel": ScheduledEvent.Channel.VOICE},
    )
    cancel = logged_in_client.post(
        reverse("scheduling_web:event-cancel", args=[event.id])
    )

    event.refresh_from_db()
    assert reschedule.status_code == 302
    assert channel.status_code == 302
    assert cancel.status_code == 302
    assert event.scheduled_for == future
    assert event.channel == ScheduledEvent.Channel.VOICE
    assert event.status == ScheduledEvent.Status.CANCELLED
    assert (event.phone_number_id, event.zip_code, event.is_demo) == original
    assert event.delivery_attempts.count() == 0


@pytest.mark.django_db
def test_terminal_event_controls_are_hidden_and_conflict_if_posted(
    logged_in_client,
    web_user,
    verified_phone,
):
    event = create_event(
        web_user,
        verified_phone,
        status=ScheduledEvent.Status.PROCESSING,
    )

    detail = logged_in_client.get(
        reverse("scheduling_web:event-detail", args=[event.id])
    )
    reschedule = logged_in_client.post(
        reverse("scheduling_web:event-reschedule", args=[event.id]),
        {"scheduled_for": (timezone.now() + timedelta(hours=2)).isoformat()},
    )

    assert detail.status_code == 200
    assert "Update time" not in detail.content.decode()
    assert "can no longer be changed" in detail.content.decode()
    assert reschedule.status_code == 409


@pytest.mark.django_db
def test_event_mutations_require_csrf(client, web_user, verified_phone):
    event = create_event(web_user, verified_phone)
    csrf_client = client.__class__(enforce_csrf_checks=True)
    csrf_client.force_login(web_user)

    create = csrf_client.post(reverse("scheduling_web:event-create"), {})
    cancel = csrf_client.post(reverse("scheduling_web:event-cancel", args=[event.id]))

    assert create.status_code == 403
    assert cancel.status_code == 403
