"""Direct-message views.

The inbox and thread render full templates; sending is an HTMX endpoint that
returns just the new message fragment so the thread appends in place. Business
rules live in ``messaging.services`` — these views resolve targets, enforce
auth, and choose what HTML (or status) to return.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Level
from moderation.services import can_participate

from messaging import services
from messaging.models import DirectMessage, DirectMessageReport
from messaging.services import (
    get_conversation,
    inbox_rows,
    mark_read,
    send_message,
)


@login_required
def inbox(request):
    return render(
        request,
        "messaging/inbox.html",
        {"rows": inbox_rows(request.user)},
    )


@login_required
def thread(request, username):
    other = get_object_or_404(get_user_model(), username=username)
    if other == request.user:
        messages.info(request, "You can't message yourself.")
        return redirect("messaging:inbox")

    conversation = get_conversation(request.user, other)
    mark_read(request.user, conversation)
    return render(
        request,
        "messaging/thread.html",
        {
            "other": other,
            "conversation": conversation,
            "messages": conversation.messages.select_related("sender"),
            "can_send": can_participate(request.user),
        },
    )


@login_required
@require_POST
def send(request, username):
    other = get_object_or_404(get_user_model(), username=username)
    body = request.POST.get("body", "")
    try:
        message = send_message(request.user, other, body)
    except PermissionDenied:
        return HttpResponse(status=403)
    except ValidationError:
        return HttpResponse(status=400)
    return render(request, "messaging/_message.html", {"message": message})


@login_required
@require_POST
def report(request, message_id):
    message = get_object_or_404(DirectMessage, pk=message_id)
    reason = request.POST.get("reason", "")
    try:
        services.report_message(request.user, message, reason)
    except PermissionDenied:
        return HttpResponse(status=403)
    except ValidationError:
        return HttpResponse(status=400)
    return render(request, "messaging/_reported.html", {})


@login_required
def reports(request):
    if not request.user.is_at_least(Level.MODERATOR):
        return HttpResponseForbidden()
    return render(
        request,
        "messaging/reports.html",
        {"reports": services.open_reports(request.user)},
    )


@login_required
@require_POST
def resolve_report(request, report_id, action):
    if not request.user.is_at_least(Level.MODERATOR):
        return HttpResponseForbidden()
    report = get_object_or_404(DirectMessageReport, pk=report_id)
    try:
        if action == "dismiss":
            services.dismiss_report(request.user, report)
        elif action == "remove":
            services.remove_reported_message(
                request.user, report, request.POST.get("reason", "")
            )
        else:
            raise Http404
    except PermissionDenied:
        return HttpResponseForbidden()
    return redirect("messaging:reports")
