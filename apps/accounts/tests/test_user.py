import pytest
from django.contrib.auth import get_user_model


@pytest.mark.django_db
def test_custom_user_can_be_created():
    user = get_user_model().objects.create_user(
        username="phase-one-user",
        password="a-secure-test-password",
    )

    assert user.username == "phase-one-user"
    assert user.check_password("a-secure-test-password")
