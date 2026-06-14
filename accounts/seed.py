"""Shared demo fake-user pool, reused by the forum and catalog content seeders.

Fake users are named "{Name} Test" (e.g. "John Test") with a deliberate mix of
derived levels — Regular, Collaborator-eligible (``accepted_submissions_count``
set, so they derive as Database Collaborator once the promotion threshold is
configured) and sticky Founders (``is_founder``) — and NEVER a Community
Moderator or Riffhub Creator (those granted roles stay manual). They are
``get_or_create``d by username, so calling this repeatedly is safe.
"""

from django.contrib.auth import get_user_model

User = get_user_model()

# (first name, level-shaping attrs). NO moderators or creators by design.
FAKE_USERS = [
    ("John", {"reputation_score": 4}),                                          # Regular
    ("Alex", {"reputation_score": 1}),                                          # Regular
    ("Pat", {"reputation_score": 6}),                                           # Regular
    ("Morgan", {"reputation_score": 2}),                                        # Regular
    ("Riley", {"reputation_score": 3}),                                         # Regular
    ("Jane", {"reputation_score": 12, "accepted_submissions_count": 6}),        # Collaborator-eligible
    ("Chris", {"reputation_score": 8, "accepted_submissions_count": 4}),        # Collaborator-eligible
    ("Jordan", {"reputation_score": 17, "accepted_submissions_count": 8}),      # Collaborator-eligible
    ("Casey", {"reputation_score": 9, "accepted_submissions_count": 5}),        # Collaborator-eligible
    ("Dana", {"reputation_score": 14, "accepted_submissions_count": 7}),        # Collaborator-eligible
    ("Sam", {"reputation_score": 28, "accepted_submissions_count": 9, "is_founder": True}),   # Founder
    ("Taylor", {"reputation_score": 22, "is_founder": True}),                   # Founder
]


def ensure_fake_users():
    """get_or_create the "{Name} Test" pool (idempotent).

    Returns ``(users, created_count)`` where ``users`` is the full pool list.
    """
    users = []
    created = 0
    for name, attrs in FAKE_USERS:
        user, was_created = User.objects.get_or_create(
            username=f"{name} Test",
            defaults={
                "email": f"{name.lower()}.test@example.com",
                "email_confirmed": True,
                **attrs,
            },
        )
        created += int(was_created)
        users.append(user)
    return users, created
