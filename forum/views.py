"""Forum views.

Read pages (index -> subtopic -> post + comments) render full templates;
the engagement actions (vote, react, comment) are HTMX endpoints returning the
re-rendered widget fragment so the page updates in place. All business rules
live in ``forum.services`` — these views just resolve targets, enforce auth,
and choose what HTML to send back.
"""

from collections import defaultdict

from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

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


def _visible_or_404(obj, user):
    """Return ``obj`` unless it's hidden content the action can't touch.

    Two gates:

    * *Author-deleted* content is closed to **all** engagement (votes, reactions,
      replies) — even for moderators — since a deleted item's reactions are only
      ever display-only. So a deleted Post/Comment (or a Comment on a deleted
      Post) raises ``Http404`` for everyone.
    * *Moderator-removed* content is hidden from non-moderators (a removed Post, a
      removed Comment, or a Comment whose parent Post is removed) but reachable by
      moderators, mirroring ``post_detail``.
    """
    is_mod = user.is_authenticated and user.is_at_least(Level.MODERATOR)
    if getattr(obj, "is_deleted", False):
        raise Http404("This content has been deleted.")
    if isinstance(obj, Comment) and obj.post.is_deleted:
        raise Http404("This content has been deleted.")
    if is_mod:
        return obj
    removed = obj.is_removed
    if isinstance(obj, Comment):
        removed = removed or obj.post.is_removed
    if removed:
        raise Http404("This content has been removed.")
    return obj


def _can_delete(user, obj) -> bool:
    """Whether ``user`` may author-delete ``obj`` (their own, still-live content)."""
    return bool(
        user.is_authenticated
        and obj.author_id == user.pk
        and not obj.is_deleted
        and not obj.is_removed
    )


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
    # A deleted item keeps its tally but is no longer votable (display-only).
    inert = getattr(obj, "is_deleted", False)
    return {
        "target_type": target_type,
        "target": obj,
        "tally": services.vote_tally(obj),
        "up_active": mine == VoteValue.UP,
        "down_active": mine == VoteValue.DOWN,
        "can_vote": user.is_authenticated and obj.author_id != user.pk and not inert,
    }


def _react_ctx(obj, user, target_type):
    # Reactions on a deleted comment are preserved and shown to everyone, but no
    # new ones can be added (PRODUCT.md) — render them as static counts.
    inert = getattr(obj, "is_deleted", False)
    return {
        "target_type": target_type,
        "target": obj,
        "counts": services.reaction_tally(obj),
        "mine": _my_reactions(user, obj),
        "palette": REACTION_PALETTE,
        "can_react": user.is_authenticated and obj.author_id != user.pk and not inert,
    }


def _comment_row(comment, user, *, replies=None, is_reply=False):
    return {
        "comment": comment,
        "vote": _vote_ctx(comment, user, "comment"),
        "react": _react_ctx(comment, user, "comment"),
        "can_delete": _can_delete(user, comment),
        "is_reply": is_reply,
        "replies": replies or [],
    }


def _build_thread(post, user):
    """Build the threaded comment rows for ``post`` honouring visibility.

    Returns a list of top-level comment rows, each carrying its one level of
    reply rows under ``replies``. Moderator-removed comments are dropped for
    non-moderators (a removed root takes its replies with it); author-deleted
    comments stay (they render as a placeholder). Mentions are prefetched so the
    ``comment_body`` filter never queries per comment.
    """
    is_mod = user.is_authenticated and user.is_at_least(Level.MODERATOR)
    qs = post.comments.select_related("author").prefetch_related("mentions")
    if not is_mod:
        qs = qs.filter(is_removed=False)
    comments = list(qs)

    replies_by_parent = defaultdict(list)
    roots = []
    for comment in comments:
        if comment.parent_id is None:
            roots.append(comment)
        else:
            replies_by_parent[comment.parent_id].append(comment)

    rows = []
    for root in roots:
        reply_rows = [
            _comment_row(reply, user, is_reply=True)
            for reply in replies_by_parent.get(root.pk, [])
        ]
        rows.append(_comment_row(root, user, replies=reply_rows))
    return rows


# --- read pages ------------------------------------------------------------
def index(request):
    topics = Topic.objects.prefetch_related("subtopics")
    return render(request, "forum/index.html", {"topics": topics})


def subtopic_detail(request, pk):
    subtopic = get_object_or_404(Subtopic.objects.select_related("topic"), pk=pk)
    posts = (
        subtopic.posts.filter(is_removed=False, is_deleted=False)
        .select_related("author")
        .annotate(
            num_comments=Count(
                "comments",
                filter=Q(comments__is_removed=False, comments__is_deleted=False),
            )
        )
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
    # A post hidden by a moderator (removed) OR by its author (deleted) is a 404
    # for everyone but moderators, who can still audit it (the /deleted area
    # links here for author-deleted posts).
    if (post.is_removed or post.is_deleted) and not is_mod:
        raise Http404("This post is no longer available.")
    comment_rows = _build_thread(post, request.user)
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
            "can_delete_post": _can_delete(request.user, post),
        },
    )


# --- engagement actions (HTMX) ---------------------------------------------
@require_POST
def vote(request, target, pk, value):
    if not request.user.is_authenticated:
        return HttpResponse("Sign in to vote.", status=403)
    obj = _visible_or_404(_resolve_target(target, pk), request.user)
    if value == "up":
        val = VoteValue.UP
    elif value == "down":
        val = VoteValue.DOWN
    else:
        return HttpResponse("Vote value must be 'up' or 'down'.", status=400)
    try:
        services.cast_vote(request.user, obj, val)
    except PermissionDenied:
        return HttpResponse("You can't vote on your own content.", status=403)
    return render(request, "forum/_vote.html", {"v": _vote_ctx(obj, request.user, target)})


@require_POST
def react(request, target, pk):
    if not request.user.is_authenticated:
        return HttpResponse("Sign in to react.", status=403)
    obj = _visible_or_404(_resolve_target(target, pk), request.user)
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
    post = _visible_or_404(get_object_or_404(Post, pk=pk), request.user)
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


@require_POST
def reply_create(request, pk):
    """Create a one-level reply to a top-level comment (HTMX).

    ``pk`` is the parent comment. Returns the reply fragment, appended under the
    parent's replies. Replying to a reply (or to deleted/removed content) is
    rejected — the single-level rule lives in ``Comment.clean``.
    """
    if not request.user.is_authenticated:
        return HttpResponse("Sign in to reply.", status=403)
    parent = _visible_or_404(
        get_object_or_404(Comment.objects.select_related("post__subtopic"), pk=pk),
        request.user,
    )
    body = (request.POST.get("body") or "").strip()
    if not body:
        return HttpResponse("A reply can't be empty.", status=400)
    try:
        reply = services.create_comment(
            post=parent.post, author=request.user, body=body, parent=parent
        )
    except PermissionDenied as exc:
        return HttpResponse(str(exc) or "You can't reply right now.", status=403)
    except ValidationError as exc:
        return HttpResponse("; ".join(exc.messages), status=400)
    return render(
        request,
        "forum/_comment.html",
        {"row": _comment_row(reply, request.user, is_reply=True)},
    )


@require_POST
def post_delete(request, pk):
    """Author-delete a post (soft) and return to its subtopic.

    Only the author may delete; the service raises ``PermissionDenied`` (→ 403)
    otherwise. After deletion the post is hidden from everyone but moderators.
    """
    if not request.user.is_authenticated:
        return redirect("login")
    post = get_object_or_404(Post, pk=pk)
    services.delete_post(request.user, post)
    messages.success(request, "Your post was deleted.")
    return redirect("forum:subtopic", pk=post.subtopic_id)


@require_POST
def comment_delete(request, pk):
    """Author-delete a comment/reply (soft) and re-render it as a placeholder.

    Returns the re-rendered comment fragment (now "This message was deleted.").
    For a top-level comment the existing replies are re-rendered with it so they
    aren't lost from the DOM on the in-place swap.
    """
    if not request.user.is_authenticated:
        return HttpResponse("Sign in to delete.", status=403)
    comment = get_object_or_404(Comment.objects.select_related("author"), pk=pk)
    services.delete_comment(request.user, comment)
    if comment.parent_id is None:
        replies_qs = comment.replies.select_related("author").prefetch_related("mentions")
        if not (request.user.is_authenticated and request.user.is_at_least(Level.MODERATOR)):
            replies_qs = replies_qs.filter(is_removed=False)
        replies = [_comment_row(r, request.user, is_reply=True) for r in replies_qs]
        row = _comment_row(comment, request.user, replies=replies)
    else:
        row = _comment_row(comment, request.user, is_reply=True)
    return render(request, "forum/_comment.html", {"row": row})


@require_GET
def comment_original(request, pk):
    """Reveal the original body of an author-deleted comment (moderators only).

    The original text is never sent to non-moderators (it isn't in the page
    HTML at all), so this gated endpoint is the only way to see it — moderators
    and Riffhub Creators (level ≥ MODERATOR) get a 200, everyone else a 403.
    """
    if not (request.user.is_authenticated and request.user.is_at_least(Level.MODERATOR)):
        raise PermissionDenied("Moderator privileges are required.")
    comment = get_object_or_404(
        Comment.objects.select_related("author").prefetch_related("mentions"), pk=pk
    )
    if not comment.is_deleted:
        raise Http404("This comment has no hidden original.")
    return render(request, "forum/_comment_original.html", {"comment": comment})


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


# --- Moderator-only audit of author-deleted posts (/deleted) ---------------
def _require_moderator(user) -> None:
    if not (user.is_authenticated and user.is_at_least(Level.MODERATOR)):
        raise PermissionDenied("Moderator privileges are required.")


def deleted_index(request):
    """List the whole live topic/subtopic tree, each subtopic annotated with how
    many author-deleted posts it holds (PRODUCT.md: moderators audit deletions).

    Moderators "see all the topics and subtopics that currently exist but they
    explore only deleted Posts" — the drill-down (``deleted_subtopic``) is where
    the deleted posts themselves live.
    """
    _require_moderator(request.user)
    topics = Topic.objects.prefetch_related(
        Prefetch(
            "subtopics",
            queryset=Subtopic.objects.annotate(
                deleted_count=Count("posts", filter=Q(posts__is_deleted=True))
            ),
        )
    )
    return render(request, "forum/deleted_index.html", {"topics": topics})


def deleted_subtopic(request, pk):
    """List the author-deleted posts within one subtopic (moderators only)."""
    _require_moderator(request.user)
    subtopic = get_object_or_404(Subtopic.objects.select_related("topic"), pk=pk)
    posts = (
        subtopic.posts.filter(is_deleted=True)
        .select_related("author")
        .annotate(num_comments=Count("comments"))
        .order_by("-deleted_at")
    )
    return render(
        request,
        "forum/deleted_subtopic.html",
        {"subtopic": subtopic, "topic": subtopic.topic, "posts": posts},
    )


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
    name_max = Subtopic._meta.get_field("name").max_length
    if not name:
        messages.error(request, "Subtopic name is required.")
    elif len(name) > name_max:
        messages.error(
            request, f"Subtopic name must be at most {name_max} characters."
        )
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
