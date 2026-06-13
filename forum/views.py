"""Forum views.

Read pages (index -> subtopic -> post + comments) render full templates;
the engagement actions (vote, react, comment) are HTMX endpoints returning the
re-rendered widget fragment so the page updates in place. All business rules
live in ``forum.services`` — these views just resolve targets, enforce auth,
and choose what HTML to send back.
"""

from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Level

from forum import services
from forum.constants import DEFAULT_CURRENCY, MARKET_DISCLAIMER_TEXT, VoteValue
from forum.forms import SubtopicForm, TopicForm
from forum.models import Attachment, Comment, Post, Reaction, Subtopic, Topic, Vote

# A small curated palette for the reaction picker (the spec allows any emoji;
# the UI offers these by default and shows whatever else has been used).
REACTION_PALETTE = ["👍", "👎", "❤️", "🔥", "🤘", "😂", "😮"]

TARGET_MODELS = {"post": Post, "comment": Comment}


# --- helpers ---------------------------------------------------------------
def _resolve_target(target_type, pk):
    model = TARGET_MODELS.get(target_type)
    if model is None:
        raise Http404("Unknown engagement target.")
    return get_object_or_404(model, pk=pk)


def _my_vote(user, obj):
    if not user.is_authenticated:
        return None
    ct = ContentType.objects.get_for_model(obj)
    row = Vote.objects.filter(voter=user, content_type=ct, object_id=obj.pk).first()
    return row.value if row else None


def _my_reactions(user, obj):
    if not user.is_authenticated:
        return set()
    ct = ContentType.objects.get_for_model(obj)
    return set(
        Reaction.objects.filter(user=user, content_type=ct, object_id=obj.pk)
        .values_list("emoji", flat=True)
    )


def _vote_ctx(obj, user, target_type):
    mine = _my_vote(user, obj)
    return {
        "target_type": target_type,
        "target": obj,
        "tally": services.vote_tally(obj),
        "up_active": mine == VoteValue.UP,
        "down_active": mine == VoteValue.DOWN,
        "can_vote": user.is_authenticated and obj.author_id != user.pk,
    }


def _react_ctx(obj, user, target_type):
    return {
        "target_type": target_type,
        "target": obj,
        "counts": services.reaction_tally(obj),
        "mine": _my_reactions(user, obj),
        "palette": REACTION_PALETTE,
        "can_react": user.is_authenticated and obj.author_id != user.pk,
    }


def _comment_row(comment, user):
    return {
        "comment": comment,
        "vote": _vote_ctx(comment, user, "comment"),
        "react": _react_ctx(comment, user, "comment"),
    }


# --- read pages ------------------------------------------------------------
def index(request):
    topics = Topic.objects.prefetch_related("subtopics")
    return render(request, "forum/index.html", {"topics": topics})


def subtopic_detail(request, pk):
    subtopic = get_object_or_404(Subtopic.objects.select_related("topic"), pk=pk)
    posts = (
        subtopic.posts.filter(is_removed=False)
        .select_related("author")
        .annotate(num_comments=Count("comments"))
    )
    disclaimer_ok = request.user.is_authenticated and services.has_accepted_market_disclaimer(
        request.user
    )
    return render(
        request,
        "forum/subtopic.html",
        {
            "subtopic": subtopic,
            "topic": subtopic.topic,
            "posts": posts,
            "is_market": subtopic.topic.is_market,
            "disclaimer_ok": disclaimer_ok,
            "disclaimer_text": MARKET_DISCLAIMER_TEXT,
            "default_currency": DEFAULT_CURRENCY,
        },
    )


def post_detail(request, pk):
    post = get_object_or_404(
        Post.objects.select_related("author", "subtopic__topic"), pk=pk
    )
    is_mod = request.user.is_authenticated and request.user.is_at_least(Level.MODERATOR)
    if post.is_removed and not is_mod:
        raise Http404("This post has been removed.")
    comments = post.comments.select_related("author")
    if not is_mod:
        comments = comments.filter(is_removed=False)
    comment_rows = [_comment_row(c, request.user) for c in comments]
    ct = ContentType.objects.get_for_model(Post)
    attachments = Attachment.objects.filter(content_type=ct, object_id=post.pk)
    return render(
        request,
        "forum/post_detail.html",
        {
            "post": post,
            "subtopic": post.subtopic,
            "topic": post.subtopic.topic,
            "comment_rows": comment_rows,
            "post_vote": _vote_ctx(post, request.user, "post"),
            "post_react": _react_ctx(post, request.user, "post"),
            "attachments": attachments,
            "is_market": post.subtopic.topic.is_market,
            "move_targets": Subtopic.objects.select_related("topic") if is_mod else None,
        },
    )


# --- engagement actions (HTMX) ---------------------------------------------
@require_POST
def vote(request, target, pk, value):
    if not request.user.is_authenticated:
        return HttpResponse("Sign in to vote.", status=403)
    obj = _resolve_target(target, pk)
    val = VoteValue.UP if value == "up" else VoteValue.DOWN
    try:
        services.cast_vote(request.user, obj, val)
    except PermissionDenied:
        return HttpResponse("You can't vote on your own content.", status=403)
    return render(request, "forum/_vote.html", {"v": _vote_ctx(obj, request.user, target)})


@require_POST
def react(request, target, pk):
    if not request.user.is_authenticated:
        return HttpResponse("Sign in to react.", status=403)
    obj = _resolve_target(target, pk)
    emoji = (request.POST.get("emoji") or "").strip()
    try:
        services.toggle_reaction(request.user, obj, emoji)
    except (PermissionDenied, ValidationError):
        return HttpResponse("Can't react.", status=400)
    return render(request, "forum/_reactions.html", {"r": _react_ctx(obj, request.user, target)})


@require_POST
def comment_create(request, pk):
    if not request.user.is_authenticated:
        return HttpResponse("Sign in to comment.", status=403)
    post = get_object_or_404(Post, pk=pk)
    body = (request.POST.get("body") or "").strip()
    if not body:
        return HttpResponse("A comment can't be empty.", status=400)
    try:
        comment = services.create_comment(post=post, author=request.user, body=body)
    except PermissionDenied as exc:
        return HttpResponse(str(exc) or "You can't comment right now.", status=403)
    return render(
        request, "forum/_comment.html", {"row": _comment_row(comment, request.user)}
    )


# --- post creation + market disclaimer -------------------------------------
@require_POST
def post_create(request, pk):
    subtopic = get_object_or_404(Subtopic.objects.select_related("topic"), pk=pk)
    if not request.user.is_authenticated:
        return redirect("login")

    title = (request.POST.get("title") or "").strip()
    body = (request.POST.get("body") or "").strip()
    if not (title and body):
        messages.error(request, "A title and body are required.")
        return redirect("forum:subtopic", pk=pk)

    extra = {}
    if request.POST.get("video_url", "").strip():
        extra["video_url"] = request.POST["video_url"].strip()

    if subtopic.topic.is_market:
        if not services.has_accepted_market_disclaimer(request.user):
            messages.error(request, "Accept the Gear Market disclaimer first.")
            return redirect("forum:subtopic", pk=pk)
        extra["price"] = request.POST.get("price") or None
        extra["currency"] = (request.POST.get("currency") or DEFAULT_CURRENCY).strip()

    try:
        post = services.create_post(
            subtopic=subtopic, author=request.user, title=title, body=body, **extra
        )
    except PermissionDenied as exc:
        messages.error(request, str(exc) or "You can't post right now.")
        return redirect("forum:subtopic", pk=pk)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("forum:subtopic", pk=pk)
    return redirect("forum:post", pk=post.pk)


@require_POST
def accept_disclaimer(request, pk):
    if request.user.is_authenticated:
        services.accept_market_disclaimer(request.user)
    return redirect("forum:subtopic", pk=pk)


# --- Creator-only topic / subtopic management ------------------------------
def _require_creator(user) -> None:
    if not (user.is_authenticated and user.is_at_least(Level.CREATOR)):
        raise PermissionDenied("Riffhub Creator privileges are required.")


def manage_topics(request):
    _require_creator(request.user)
    return render(
        request,
        "forum/manage/topics.html",
        {"topics": Topic.objects.prefetch_related("subtopics")},
    )


@require_POST
def topic_create(request):
    _require_creator(request.user)
    form = TopicForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(f"{f}: {e.as_text()}" for f, e in form.errors.items()))
    else:
        try:
            with transaction.atomic():
                topic = form.save()
        except IntegrityError:
            messages.error(request, "A topic with a conflicting name already exists.")
        else:
            messages.success(request, f"Created topic “{topic.name}”.")
    return redirect("forum:manage_topics")


def topic_edit(request, pk):
    _require_creator(request.user)
    topic = get_object_or_404(Topic, pk=pk)
    if request.method == "POST":
        form = TopicForm(request.POST, instance=topic)
        if form.is_valid():
            form.save()
            messages.success(request, "Topic updated.")
            return redirect("forum:manage_topics")
    else:
        form = TopicForm(instance=topic)
    return render(request, "forum/manage/topic_form.html", {"form": form, "topic": topic})


@require_POST
def topic_delete(request, pk):
    _require_creator(request.user)
    topic = get_object_or_404(Topic, pk=pk)
    name = topic.name
    topic.delete()
    messages.success(request, f"Deleted topic “{name}” and everything under it.")
    return redirect("forum:manage_topics")


@require_POST
def subtopic_create(request, pk):
    _require_creator(request.user)
    topic = get_object_or_404(Topic, pk=pk)
    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "Subtopic name is required.")
    else:
        try:
            with transaction.atomic():
                Subtopic.objects.create(topic=topic, name=name)
        except IntegrityError:
            messages.error(request, f"“{name}” already exists under {topic.name}.")
        else:
            messages.success(request, f"Added “{name}” to {topic.name}.")
    return redirect("forum:manage_topics")


def subtopic_edit(request, pk):
    _require_creator(request.user)
    subtopic = get_object_or_404(Subtopic, pk=pk)
    if request.method == "POST":
        form = SubtopicForm(request.POST, instance=subtopic)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
            except IntegrityError:
                messages.error(request, "That name already exists under the chosen topic.")
            else:
                messages.success(request, "Subtopic updated.")
                return redirect("forum:manage_topics")
    else:
        form = SubtopicForm(instance=subtopic)
    return render(
        request, "forum/manage/subtopic_form.html", {"form": form, "subtopic": subtopic}
    )


@require_POST
def subtopic_delete(request, pk):
    _require_creator(request.user)
    subtopic = get_object_or_404(Subtopic, pk=pk)
    name = subtopic.name
    subtopic.delete()
    messages.success(request, f"Deleted subtopic “{name}”.")
    return redirect("forum:manage_topics")
