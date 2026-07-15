from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.scheduling.models import ScheduledEvent
from apps.scheduling.serializers import (
    ChangeScheduledEventChannelSerializer,
    RescheduleScheduledEventSerializer,
    ScheduledEventSerializer,
)
from apps.scheduling.services import (
    ScheduledEventLifecycleConflict,
    cancel_user_scheduled_event,
    change_user_scheduled_event_channel,
    reschedule_user_scheduled_event,
)


def _validation_detail(exc):
    return exc.message_dict if hasattr(exc, "message_dict") else exc.messages


class UserEventQuerysetMixin:
    def get_queryset(self):
        return ScheduledEvent.objects.filter(user=self.request.user).select_related(
            "phone_number"
        )


class ScheduledEventListCreateView(
    UserEventQuerysetMixin,
    generics.ListCreateAPIView,
):
    serializer_class = ScheduledEventSerializer


class ScheduledEventDetailView(
    UserEventQuerysetMixin,
    generics.RetrieveAPIView,
):
    serializer_class = ScheduledEventSerializer
    lookup_url_kwarg = "event_id"


class ScheduledEventCancelView(APIView):
    def post(self, request, event_id):
        try:
            event = cancel_user_scheduled_event(event_id, user=request.user)
        except ScheduledEvent.DoesNotExist:
            raise Http404 from None
        except ScheduledEventLifecycleConflict as exc:
            return Response(_validation_detail(exc), status=status.HTTP_409_CONFLICT)

        return Response(ScheduledEventSerializer(event).data)


class ScheduledEventRescheduleView(APIView):
    def post(self, request, event_id):
        serializer = RescheduleScheduledEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            event = reschedule_user_scheduled_event(
                event_id,
                user=request.user,
                scheduled_for=serializer.validated_data["scheduled_for"],
            )
        except ScheduledEvent.DoesNotExist:
            raise Http404 from None
        except ScheduledEventLifecycleConflict as exc:
            return Response(_validation_detail(exc), status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response(_validation_detail(exc), status=status.HTTP_400_BAD_REQUEST)

        return Response(ScheduledEventSerializer(event).data)


class ScheduledEventChannelView(APIView):
    def post(self, request, event_id):
        serializer = ChangeScheduledEventChannelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            event = change_user_scheduled_event_channel(
                event_id,
                user=request.user,
                channel=serializer.validated_data["channel"],
            )
        except ScheduledEvent.DoesNotExist:
            raise Http404 from None
        except ScheduledEventLifecycleConflict as exc:
            return Response(_validation_detail(exc), status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            return Response(_validation_detail(exc), status=status.HTTP_400_BAD_REQUEST)

        return Response(ScheduledEventSerializer(event).data)
