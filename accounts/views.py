"""Account views: self-serve sign-up and e-mail confirmation.

Sign-in/out are handled by Django's built-in auth views (wired in
``config.urls``). Confirming an e-mail is what unlocks collab-db contributions
(see ``catalog.services.can_submit_to_collab``); forum participation does not
require it, so we log the user in immediately on sign-up.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.forms import SignUpForm
from accounts.models import EmailConfirmation
from catalog.models import GuitarModel
from forum.models import Comment, Post
from moderation.models import Silence

AUTH_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _send_confirmation(request, user) -> str:
    """Create a fresh confirmation token, e-mail the link, return it.

    The link is returned so DEBUG flows can surface it without a real mailbox.
    """
    confirmation = EmailConfirmation.objects.create(user=user)
    link = request.build_absolute_uri(
        reverse("confirm_email", args=[confirmation.token])
    )
    send_mail(
        subject="Confirm your riffhub e-mail",
        message=(
            "Welcome to riffhub!\n\n"
            "Confirm your e-mail to unlock contributions to the gear database:\n\n"
            f"{link}\n"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )
    return link


def signup(request):
    if request.user.is_authenticated:
        return redirect("forum:index")

    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            link = _send_confirmation(request, user)
            login(request, user, backend=AUTH_BACKEND)
            return render(
                request,
                "registration/signup_done.html",
                {"email": user.email, "confirmation_link": link if settings.DEBUG else None},
            )
    else:
        form = SignUpForm()
    return render(request, "registration/signup.html", {"form": form})


def confirm_email(request, token):
    confirmation = get_object_or_404(EmailConfirmation, token=token)
    if confirmation.confirmed_at is None:
        confirmation.confirm()
        messages.success(
            request, "E-mail confirmed — you can now contribute to the database."
        )
    else:
        messages.info(request, "That e-mail was already confirmed.")
    return redirect("forum:index")


@require_POST
def resend_confirmation(request):
    user = request.user
    if user.is_authenticated and not user.email_confirmed:
        link = _send_confirmation(request, user)
        if settings.DEBUG:
            messages.info(request, f"Confirmation re-sent. Dev link: {link}")
        else:
            messages.success(request, "Confirmation e-mail re-sent.")
    return redirect(request.META.get("HTTP_REFERER") or "forum:index")


def profile(request, username):
    """Public profile: standing (level, reputation, role badges), the public
    silence flag (if any), and recent DB contributions + forum activity."""
    profile_user = get_object_or_404(get_user_model(), username=username)
    posts = (
        Post.objects.filter(author=profile_user, is_removed=False)
        .select_related("subtopic__topic")
        .order_by("-created_at")[:10]
    )
    guitars = (
        GuitarModel.objects.published()
        .filter(submitted_by=profile_user)
        .select_related("brand")
        .order_by("-published_at")[:10]
    )
    return render(
        request,
        "accounts/profile.html",
        {
            "profile_user": profile_user,
            "level": profile_user.level,
            "posts": posts,
            "post_count": Post.objects.filter(author=profile_user, is_removed=False).count(),
            "comment_count": Comment.objects.filter(
                author=profile_user, is_removed=False
            ).count(),
            "guitars": guitars,
            "public_silence": Silence.objects.filter(
                target=profile_user, is_public_flag=True
            ).first(),
            "is_self": request.user.is_authenticated
            and request.user.pk == profile_user.pk,
        },
    )
