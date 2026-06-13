from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel

from forum.constants import ProposalStatus, ProposalVoteValue


class BaseProposal(TimeStampedModel):
    """Shared fields/behaviour for community topic & subtopic proposals.

    PRODUCT.md: only Database Collaborators or higher may *propose* (gated in
    ``forum.services``), any non-anonymous user may *vote*. A proposal is open
    for a configurable window then accepted if its positive-vote ratio clears
    the configured pass ratio (see ``forum.services.evaluate_proposal``).
    """

    proposer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="%(class)ss",
    )
    proposed_name = models.CharField(max_length=100)
    proposed_description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=ProposalStatus.choices,
        default=ProposalStatus.OPEN,
        db_index=True,
    )
    opened_at = models.DateTimeField(auto_now_add=True)
    # Set from SiteConfiguration.topic_proposal_voting_days at creation time
    # (see forum.services.open_topic_proposal / open_subtopic_proposal).
    closes_at = models.DateTimeField()

    class Meta:
        abstract = True
        ordering = ["-opened_at"]


class TopicProposal(BaseProposal):
    """A community proposal for a brand-new top-level topic."""

    def __str__(self) -> str:
        return f"Topic proposal: {self.proposed_name} ({self.status})"


class SubtopicProposal(BaseProposal):
    """A community proposal for a new subtopic under an existing topic."""

    parent_topic = models.ForeignKey(
        "forum.Topic", on_delete=models.CASCADE, related_name="subtopic_proposals"
    )

    def __str__(self) -> str:
        return (
            f"Subtopic proposal: {self.parent_topic} / "
            f"{self.proposed_name} ({self.status})"
        )


class ProposalVote(TimeStampedModel):
    """A for/against vote on a topic or subtopic proposal.

    Modelled with two nullable FKs (exactly one set, enforced by a check
    constraint) rather than a generic relation: there are only two concrete
    proposal types, so this keeps cascade/integrity simple and queries plain.
    A voter may cast at most one vote per proposal (unique constraints).
    """

    voter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="forum_proposal_votes",
    )
    topic_proposal = models.ForeignKey(
        "forum.TopicProposal",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="votes",
    )
    subtopic_proposal = models.ForeignKey(
        "forum.SubtopicProposal",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="votes",
    )
    value = models.IntegerField(choices=ProposalVoteValue.choices)

    class Meta:
        constraints = [
            # Exactly one proposal target must be set.
            models.CheckConstraint(
                condition=(
                    Q(topic_proposal__isnull=False, subtopic_proposal__isnull=True)
                    | Q(
                        topic_proposal__isnull=True,
                        subtopic_proposal__isnull=False,
                    )
                ),
                name="proposalvote_exactly_one_target",
            ),
            # One vote per voter per proposal (split by target type).
            models.UniqueConstraint(
                fields=["voter", "topic_proposal"],
                condition=Q(topic_proposal__isnull=False),
                name="unique_vote_per_voter_per_topic_proposal",
            ),
            models.UniqueConstraint(
                fields=["voter", "subtopic_proposal"],
                condition=Q(subtopic_proposal__isnull=False),
                name="unique_vote_per_voter_per_subtopic_proposal",
            ),
        ]

    def __str__(self) -> str:
        target = self.topic_proposal or self.subtopic_proposal
        return f"{self.voter} voted {self.get_value_display()} on {target}"
