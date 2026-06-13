"""Direct-message views.

The inbox and thread render full templates; sending is an HTMX endpoint that
returns just the new message fragment so the thread appends in place. Business
rules live in ``messaging.services`` — these views resolve targets, enforce
auth, and choose what HTML (or status) to return.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from moderation.services import can_participate

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
