from django.urls import path

from apps.scheduling.views import (
    ScheduledEventCancelView,
    ScheduledEventChannelView,
    ScheduledEventDetailView,
    ScheduledEventListCreateView,
    ScheduledEventRescheduleView,
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
    path(
        "events/<int:event_id>/reschedule/",
        ScheduledEventRescheduleView.as_view(),
        name="event-reschedule",
    ),
    path(
        "events/<int:event_id>/channel/",
        ScheduledEventChannelView.as_view(),
        name="event-channel",
    ),
]
