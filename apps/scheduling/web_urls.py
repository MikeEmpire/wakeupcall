from django.urls import path

from apps.scheduling import web_views

app_name = "scheduling_web"

urlpatterns = [
    path("", web_views.home, name="home"),
    path("events/", web_views.event_list, name="event-list"),
    path("events/new/", web_views.event_create, name="event-create"),
    path("events/<int:event_id>/", web_views.event_detail, name="event-detail"),
    path(
        "events/<int:event_id>/reschedule/",
        web_views.event_reschedule,
        name="event-reschedule",
    ),
    path(
        "events/<int:event_id>/channel/",
        web_views.event_channel,
        name="event-channel",
    ),
    path(
        "events/<int:event_id>/cancel/",
        web_views.event_cancel,
        name="event-cancel",
    ),
]
