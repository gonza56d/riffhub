"""URL patterns for the collab-db review / voting UI.

No ``app_name`` — the orchestrator includes these under the existing
``catalog`` namespace, so templates reference them as
``{% url 'catalog:review_queue' %}`` etc.
"""

from django.urls import path

from catalog import views_review

urlpatterns = [
    path("", views_review.review_queue, name="review_queue"),
    path("<str:kind>/<int:pk>/", views_review.review_detail, name="review_detail"),
    path(
        "<str:kind>/<int:pk>/vote/<str:value>/",
        views_review.review_vote,
        name="review_vote",
    ),
    path(
        "<str:kind>/<int:pk>/correct/",
        views_review.add_correction,
        name="review_correct",
    ),
]
