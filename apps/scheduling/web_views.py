from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.serializers import mask_phone_number
from apps.scheduling.forms import (
    ChangeEventChannelForm,
    RescheduleEventForm,
    ScheduledEventCreateForm,
)
from apps.scheduling.models import ScheduledEvent
from apps.scheduling.services import (
    ScheduledEventLifecycleConflict,
    cancel_user_scheduled_event,
    change_user_scheduled_event_channel,
    create_scheduled_event,
    reschedule_user_scheduled_event,
)


def _event_for_user(request, event_id):
    return get_object_or_404(
        ScheduledEvent.objects.select_related("phone_number"),
        id=event_id,
        user=request.user,
    )


def _render_event_detail(
    request,
    event,
    *,
    reschedule_form=None,
    channel_form=None,
    status=200,
):
    return render(
        request,
        "scheduling/event_detail.html",
        {
            "event": event,
            "masked_number": mask_phone_number(event.phone_number.number),
            "reschedule_form": reschedule_form or RescheduleEventForm(),
            "channel_form": channel_form
            or ChangeEventChannelForm(initial={"channel": event.channel}),
            "can_change": event.status == ScheduledEvent.Status.SCHEDULED,
        },
        status=status,
    )


@login_required
@require_GET
def home(request):
    return redirect("scheduling_web:event-list")


@login_required
@require_GET
def event_list(request):
    events = ScheduledEvent.objects.filter(user=request.user).select_related(
        "phone_number"
    )
    return render(
        request,
        "scheduling/event_list.html",
        {
            "event_rows": [
                (event, mask_phone_number(event.phone_number.number))
                for event in events
            ]
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def event_create(request):
    form = ScheduledEventCreateForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            event = create_scheduled_event(
                user=request.user,
                phone_number_id=form.cleaned_data["phone_number"].id,
                zip_code=form.cleaned_data["zip_code"],
                scheduled_for=form.cleaned_data["scheduled_for"],
                channel=form.cleaned_data["channel"],
            )
        except DjangoValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(request, "Demo wake-up event scheduled.")
            return redirect("scheduling_web:event-detail", event_id=event.id)

    return render(
        request,
        "scheduling/event_form.html",
        {"form": form},
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_GET
def event_detail(request, event_id):
    return _render_event_detail(request, _event_for_user(request, event_id))


@login_required
@require_POST
def event_reschedule(request, event_id):
    event = _event_for_user(request, event_id)
    form = RescheduleEventForm(request.POST)
    if not form.is_valid():
        return _render_event_detail(request, event, reschedule_form=form, status=400)
    try:
        event = reschedule_user_scheduled_event(
            event.id,
            user=request.user,
            scheduled_for=form.cleaned_data["scheduled_for"],
        )
    except ScheduledEventLifecycleConflict as exc:
        form.add_error(None, "; ".join(exc.messages))
        event.refresh_from_db()
        return _render_event_detail(request, event, reschedule_form=form, status=409)
    messages.success(request, "Event rescheduled.")
    return redirect("scheduling_web:event-detail", event_id=event.id)


@login_required
@require_POST
def event_channel(request, event_id):
    event = _event_for_user(request, event_id)
    form = ChangeEventChannelForm(request.POST)
    if not form.is_valid():
        return _render_event_detail(request, event, channel_form=form, status=400)
    try:
        event = change_user_scheduled_event_channel(
            event.id,
            user=request.user,
            channel=form.cleaned_data["channel"],
        )
    except ScheduledEventLifecycleConflict as exc:
        form.add_error(None, "; ".join(exc.messages))
        event.refresh_from_db()
        return _render_event_detail(request, event, channel_form=form, status=409)
    messages.success(request, "Delivery channel updated.")
    return redirect("scheduling_web:event-detail", event_id=event.id)


@login_required
@require_POST
def event_cancel(request, event_id):
    event = _event_for_user(request, event_id)
    try:
        event = cancel_user_scheduled_event(event.id, user=request.user)
    except ScheduledEventLifecycleConflict as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Event cancelled.")
    return redirect("scheduling_web:event-detail", event_id=event.id)
