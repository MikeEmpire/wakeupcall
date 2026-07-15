from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.scheduling.models import ScheduledEvent
from apps.scheduling.serializers import ScheduledEventSerializer
from apps.scheduling.services import cancel_user_scheduled_event


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
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            return Response(detail, status=status.HTTP_409_CONFLICT)

        return Response(ScheduledEventSerializer(event).data)
