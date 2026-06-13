"""Behavioural services for the forum domain.

Business rules from PRODUCT.md live here (not in views or models) so they can
be reused by HTMX views, the admin, management commands and tests:

* voting       — mutually-exclusive up/down, no self-votes, toggle on re-cast
* reactions    — one of each emoji per target, no self-reactions, toggle off
* activity     — any action bumps the subtopic and its parent topic by +1
* proposals    — open a window, tally votes, accept on pass-ratio after close

Integration seam: who may *propose* a topic/subtopic is "Database Collaborator
or higher". The level system lives in the ``accounts`` app (built by another
worker), so that check is left as a clearly-marked TODO and we fall back to
"is_authenticated" for now.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import Level
from core.models import SiteConfiguration

from forum.constants import (
    ACTIVITY_INCREMENT,
    REP_COMMENT_CREATED,
    REP_POST_CREATED,
    REP_RECEIVED_DOWNVOTE,
    REP_RECEIVED_UPVOTE,
    ProposalStatus,
    ProposalVoteValue,
    VoteValue,
)
from forum.models import (
    Comment,
    Post,
    ProposalVote,
    Reaction,
    SubtopicProposal,
    TopicProposal,
    Vote,
)


# ---------------------------------------------------------------------------
# Activity sorting
# ---------------------------------------------------------------------------
def register_activity(subtopic) -> None:
    """Bump activity on ``subtopic`` and its parent topic by one.

    PRODUCT.md: topics/subtopics are sorted by activity and ANY action (post,
    comment, vote, react) counts as +1. Uses an atomic ``F`` update so
    concurrent actions don't lose increments.
    """
    from django.db.models import F

    type(subtopic).objects.filter(pk=subtopic.pk).update(
        activity_count=F("activity_count") + ACTIVITY_INCREMENT
    )
    topic = subtopic.topic
    type(topic).objects.filter(pk=topic.pk).update(
        activity_count=F("activity_count") + ACTIVITY_INCREMENT
    )


def _subtopic_of(target) -> object:
    """Resolve the owning subtopic for a Post or Comment target."""
    if isinstance(target, Post):
        return target.subtopic
    if isinstance(target, Comment):
        return target.post.subtopic
    raise TypeError(f"Unsupported activity target: {type(target).__name__}")


# ---------------------------------------------------------------------------
# Content creation (wrap so activity is always registered)
# ---------------------------------------------------------------------------
@transaction.atomic
def create_post(*, subtopic, author, title: str, body: str, **extra) -> Post:
    """Create a post, validating market/price rules and registering activity.

    ``extra`` may carry ``video_url`` and (Gear Market only) ``price`` /
    ``currency``. ``full_clean`` runs ``Post.clean`` so the price/market
    coupling is enforced here too, not just in the admin.
    """
    post = Post(subtopic=subtopic, author=author, title=title, body=body, **extra)
    post.full_clean()
    post.save()
    register_activity(subtopic)
    author.add_reputation(REP_POST_CREATED)
    return post


@transaction.atomic
def create_comment(*, post, author, body: str, **extra) -> Comment:
    """Create a comment and register activity on its subtopic/topic."""
    comment = Comment(post=post, author=author, body=body, **extra)
    comment.full_clean()
    comment.save()
    register_activity(post.subtopic)
    author.add_reputation(REP_COMMENT_CREATED)
    return comment


# ---------------------------------------------------------------------------
# Voting
# ---------------------------------------------------------------------------
def _vote_reputation(value: int) -> int:
    """Reputation delta a content author earns from one received vote."""
    return REP_RECEIVED_UPVOTE if value == VoteValue.UP else REP_RECEIVED_DOWNVOTE


@transaction.atomic
def cast_vote(user, target, value: int) -> Vote | None:
    """Cast (or toggle/replace) a vote on a Post or Comment.

    PRODUCT.md rules:
      * you cannot vote your own post/comment -> ``PermissionDenied``
      * up and down are mutually exclusive
      * re-casting the SAME value removes the vote (toggle off)
      * casting the OPPOSITE value replaces the existing one

    Returns the resulting :class:`Vote`, or ``None`` if the vote was toggled
    off. Any change counts as activity.
    """
    value = int(value)
    if value not in (VoteValue.UP, VoteValue.DOWN):
        raise ValidationError("Vote value must be +1 (up) or -1 (down).")

    if target.author_id == getattr(user, "pk", None):
        raise PermissionDenied("You cannot vote on your own content.")

    content_type = ContentType.objects.get_for_model(
        target, for_concrete_model=False
    )
    existing = (
        Vote.objects.select_for_update()
        .filter(voter=user, content_type=content_type, object_id=target.pk)
        .first()
    )

    if existing is not None:
        if existing.value == value:
            # Same value again -> toggle the vote off (reverse its rep effect).
            existing.delete()
            target.author.add_reputation(-_vote_reputation(existing.value))
            register_activity(_subtopic_of(target))
            return None
        # Opposite value -> replace (up/down mutually exclusive).
        target.author.add_reputation(
            _vote_reputation(value) - _vote_reputation(existing.value)
        )
        existing.value = value
        existing.save(update_fields=["value", "updated_at"])
        register_activity(_subtopic_of(target))
        return existing

    vote = Vote.objects.create(
        voter=user,
        content_type=content_type,
        object_id=target.pk,
        value=value,
    )
    target.author.add_reputation(_vote_reputation(value))
    register_activity(_subtopic_of(target))
    return vote


def vote_tally(target) -> dict:
    """Return ``{"up": n, "down": m}`` for a Post or Comment.

    PRODUCT.md asks positives and negatives to be counted *separately* so the
    UI can decide how to display them later (net score, ratio, both, ...).
    """
    content_type = ContentType.objects.get_for_model(
        target, for_concrete_model=False
    )
    base = Vote.objects.filter(content_type=content_type, object_id=target.pk)
    return {
        "up": base.filter(value=VoteValue.UP).count(),
        "down": base.filter(value=VoteValue.DOWN).count(),
    }


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------
@transaction.atomic
def toggle_reaction(user, target, emoji: str) -> Reaction | None:
    """Add or remove an emoji reaction on a Post or Comment.

    PRODUCT.md rules:
      * you cannot react to your own post/comment -> ``PermissionDenied``
      * one of each emoji type per user per target
      * clicking an emoji you already used removes it (toggle off)
      * a user may stack many *different* emojis on one target

    Returns the created :class:`Reaction`, or ``None`` when toggled off. Any
    change counts as activity.
    """
    emoji = (emoji or "").strip()
    if not emoji:
        raise ValidationError("An emoji is required to react.")

    if target.author_id == getattr(user, "pk", None):
        raise PermissionDenied("You cannot react to your own content.")

    content_type = ContentType.objects.get_for_model(
        target, for_concrete_model=False
    )
    existing = (
        Reaction.objects.select_for_update()
        .filter(
            user=user,
            content_type=content_type,
            object_id=target.pk,
            emoji=emoji,
        )
        .first()
    )
    if existing is not None:
        # Same emoji again -> remove it.
        existing.delete()
        register_activity(_subtopic_of(target))
        return None

    reaction = Reaction.objects.create(
        user=user,
        content_type=content_type,
        object_id=target.pk,
        emoji=emoji,
    )
    register_activity(_subtopic_of(target))
    return reaction


def reaction_tally(target) -> dict:
    """Return ``{emoji: count}`` for every emoji used on ``target``."""
    from django.db.models import Count

    content_type = ContentType.objects.get_for_model(
        target, for_concrete_model=False
    )
    rows = (
        Reaction.objects.filter(content_type=content_type, object_id=target.pk)
        .values("emoji")
        .annotate(count=Count("id"))
    )
    return {row["emoji"]: row["count"] for row in rows}


# ---------------------------------------------------------------------------
# Gear Market disclaimer
# ---------------------------------------------------------------------------
def accept_market_disclaimer(user):
    """Record (idempotently) that ``user`` accepted the Gear Market disclaimer.

    PRODUCT.md: a user must accept the "riffhub is not responsible …"
    condition before participating in the selling section.
    """
    from forum.models import MarketDisclaimerAcceptance

    acceptance, _ = MarketDisclaimerAcceptance.objects.get_or_create(user=user)
    return acceptance


def has_accepted_market_disclaimer(user) -> bool:
    """Whether ``user`` has accepted the Gear Market disclaimer."""
    from forum.models import MarketDisclaimerAcceptance

    if not getattr(user, "is_authenticated", False):
        return False
    return MarketDisclaimerAcceptance.objects.filter(user=user).exists()


# ---------------------------------------------------------------------------
# Community topic / subtopic proposals
# ---------------------------------------------------------------------------
def _ensure_may_propose(user) -> None:
    """Guard who may open a proposal (PRODUCT.md).

    Proposing is restricted to "Database Collaborators or higher". The level
    system is owned by the accounts app (another worker), so for now we only
    require an authenticated user and leave the real gate as a TODO.
    """
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied("You must be signed in to propose.")
    if not user.is_at_least(Level.COLLABORATOR):
        raise PermissionDenied(
            "Only Database Collaborators and above can propose topics or subtopics."
        )


def _ensure_proposals_enabled(config: SiteConfiguration) -> None:
    """Reject proposing while the feature is toggled off (Riffhub Creators can
    disable it at any time, per PRODUCT.md)."""
    if not config.topic_proposals_enabled:
        raise PermissionDenied("Topic/subtopic proposals are currently disabled.")


def _proposal_window_close(config: SiteConfiguration):
    from datetime import timedelta

    return timezone.now() + timedelta(days=config.topic_proposal_voting_days)


@transaction.atomic
def open_topic_proposal(user, *, name: str, description: str = "") -> TopicProposal:
    """Open a new top-level topic proposal with a config-driven voting window."""
    _ensure_may_propose(user)
    config = SiteConfiguration.get_solo()
    _ensure_proposals_enabled(config)
    return TopicProposal.objects.create(
        proposer=user,
        proposed_name=name,
        proposed_description=description,
        closes_at=_proposal_window_close(config),
    )


@transaction.atomic
def open_subtopic_proposal(
    user, *, parent_topic, name: str, description: str = ""
) -> SubtopicProposal:
    """Open a new subtopic proposal under ``parent_topic``."""
    _ensure_may_propose(user)
    config = SiteConfiguration.get_solo()
    _ensure_proposals_enabled(config)
    return SubtopicProposal.objects.create(
        proposer=user,
        parent_topic=parent_topic,
        proposed_name=name,
        proposed_description=description,
        closes_at=_proposal_window_close(config),
    )


@transaction.atomic
def cast_proposal_vote(user, proposal, value: int) -> ProposalVote:
    """Cast/replace a for-against vote on a proposal.

    PRODUCT.md: ANY non-anonymous user may vote here. One vote per voter per
    proposal — re-voting updates the existing value.
    """
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied("You must be signed in to vote.")

    value = int(value)
    if value not in (ProposalVoteValue.UP, ProposalVoteValue.DOWN):
        raise ValidationError("Proposal vote value must be +1 or -1.")
    if proposal.status != ProposalStatus.OPEN:
        raise PermissionDenied("Voting on this proposal is closed.")

    target_field = (
        "topic_proposal"
        if isinstance(proposal, TopicProposal)
        else "subtopic_proposal"
    )
    vote, created = ProposalVote.objects.select_for_update().get_or_create(
        voter=user,
        **{target_field: proposal},
        defaults={"value": value},
    )
    if not created and vote.value != value:
        vote.value = value
        vote.save(update_fields=["value", "updated_at"])
    return vote


def _proposal_votes(proposal):
    return ProposalVote.objects.filter(
        topic_proposal=proposal
        if isinstance(proposal, TopicProposal)
        else None,
        subtopic_proposal=proposal
        if isinstance(proposal, SubtopicProposal)
        else None,
    )


def proposal_tally(proposal) -> dict:
    """Return ``{"up": n, "down": m}`` for a proposal (counted separately)."""
    votes = _proposal_votes(proposal)
    return {
        "up": votes.filter(value=ProposalVoteValue.UP).count(),
        "down": votes.filter(value=ProposalVoteValue.DOWN).count(),
    }


@transaction.atomic
def evaluate_proposal(proposal):
    """Close and resolve a proposal once its voting window has elapsed.

    PRODUCT.md: after the window, accept if the positive-vote ratio meets or
    exceeds the configured pass ratio (default 75%). A proposal with no votes
    cannot pass. Accepting a proposal materialises the real Topic/Subtopic.

    Returns the (possibly updated) proposal. No-ops if it's already resolved or
    the window is still open.
    """
    if proposal.status != ProposalStatus.OPEN:
        return proposal
    if timezone.now() < proposal.closes_at:
        # Window still open — nothing to decide yet.
        return proposal

    config = SiteConfiguration.get_solo()
    pass_ratio = Decimal(config.topic_proposal_pass_ratio)

    tally = proposal_tally(proposal)
    total = tally["up"] + tally["down"]
    ratio = (Decimal(tally["up"]) / Decimal(total)) if total else Decimal(0)

    if total > 0 and ratio >= pass_ratio:
        proposal.status = ProposalStatus.ACCEPTED
        _materialise_proposal(proposal)
    else:
        proposal.status = ProposalStatus.REJECTED
    proposal.save(update_fields=["status", "updated_at"])
    return proposal


def _materialise_proposal(proposal) -> None:
    """Create the real Topic/Subtopic for a freshly accepted proposal."""
    from forum.models import Subtopic, Topic

    if isinstance(proposal, TopicProposal):
        Topic.objects.get_or_create(
            name=proposal.proposed_name,
            defaults={"description": proposal.proposed_description},
        )
    else:
        Subtopic.objects.get_or_create(
            topic=proposal.parent_topic,
            name=proposal.proposed_name,
        )
