from django.urls import path

from apps.scheduling.views import (
    ScheduledEventCancelView,
    ScheduledEventDetailView,
    ScheduledEventListCreateView,
)

app_name = "scheduling"

urlpatterns = [
    path("events/", ScheduledEventListCreateView.as_view(), name="event-list"),
    path(
        "events/<int:event_id>/",
        ScheduledEventDetailView.as_view(),
        name="event-detail",
    ),
    path(
        "events/<int:event_id>/cancel/",
        ScheduledEventCancelView.as_view(),
        name="event-cancel",
    ),
]
