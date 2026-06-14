"""Moderator-facing views: a dashboard + POST action endpoints.

All endpoints require Community Moderator level or higher; the underlying rules
(who can be silenced/banned, escalation, soft-remove) live in
``moderation.services``. Actions redirect back with a message.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.models import Level
from forum.models import Comment, Post, Subtopic
from moderation import services
from moderation.models import Ban, ContentAction, Silence, Warning

User = get_user_model()
CONTENT_MODELS = {"post": Post, "comment": Comment}


def _require_moderator(user) -> None:
    if not (user.is_authenticated and user.is_at_least(Level.MODERATOR)):
        raise PermissionDenied("Moderator privileges are required.")


def _back(request, default="moderation:dashboard"):
    referer = request.META.get("HTTP_REFERER")
    if referer and url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(referer)
    return redirect(reverse(default))


def dashboard(request):
    _require_moderator(request.user)
    now = timezone.now()
    return render(request, "moderation/dashboard.html", {
        "active_silences": Silence.objects.filter(
            Q(is_permanent=True) | Q(ends_at__gt=now)
        ).select_related("target", "issued_by"),
        "active_bans": Ban.objects.filter(lifted_at__isnull=True).select_related(
            "target", "issued_by"
        ),
        "actions": ContentAction.objects.select_related("moderator")[:40],
        "warnings": Warning.objects.select_related("target", "issued_by")[:40],
    })


def _reason(request) -> str:
    return (request.POST.get("reason") or "").strip()


@require_POST
def warn_user(request, pk):
    _require_moderator(request.user)
    target = get_object_or_404(User, pk=pk)
    try:
        services.warn(request.user, target, _reason(request) or "(no reason given)")
        messages.success(request, f"Warned {target.username}.")
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    return _back(request)


@require_POST
def silence_user(request, pk):
    _require_moderator(request.user)
    target = get_object_or_404(User, pk=pk)
    try:
        s = services.silence(request.user, target, _reason(request) or "(no reason given)")
        when = "permanently" if s.is_permanent else f"until {s.ends_at:%Y-%m-%d}"
        messages.success(request, f"Silenced {target.username} {when} (silence #{s.sequence}).")
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    return _back(request)


@require_POST
def ban_user(request, pk):
    _require_moderator(request.user)
    target = get_object_or_404(User, pk=pk)
    try:
        services.ban(request.user, target, _reason(request) or "(no reason given)")
        messages.success(request, f"Banned {target.username}.")
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    return _back(request)


@require_POST
def lift_ban_user(request, pk):
    _require_moderator(request.user)
    target = get_object_or_404(User, pk=pk)
    try:
        services.lift_ban(request.user, target)
        messages.success(request, f"Lifted the ban on {target.username}.")
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    return _back(request)


@require_POST
def move_post(request, pk):
    _require_moderator(request.user)
    post = get_object_or_404(Post, pk=pk)
    try:
        subtopic_pk = int(request.POST.get("subtopic") or "")
    except (TypeError, ValueError):
        raise Http404("Invalid subtopic.")
    to = get_object_or_404(Subtopic, pk=subtopic_pk)
    try:
        services.move_content(request.user, post, to, _reason(request))
        messages.success(request, f"Moved “{post.title}” to {to}.")
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    return redirect("forum:post", pk=post.pk)


def _content_obj(kind, pk):
    model = CONTENT_MODELS.get(kind)
    if model is None:
        raise Http404("Unknown content kind.")
    return get_object_or_404(model, pk=pk)


@require_POST
def remove_content(request, kind, pk):
    _require_moderator(request.user)
    obj = _content_obj(kind, pk)
    try:
        services.remove_content(request.user, obj, _reason(request))
        messages.success(request, f"Removed {kind}.")
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    if kind == "post":
        return redirect("forum:subtopic", pk=obj.subtopic_id)
    return redirect("forum:post", pk=obj.post_id)


@require_POST
def restore_content(request, kind, pk):
    _require_moderator(request.user)
    obj = _content_obj(kind, pk)
    try:
        services.restore_content(request.user, obj)
        messages.success(request, f"Restored {kind}.")
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    return _back(request)
