"""URL patterns for the collab-db SUBMISSION UI.

No ``app_name`` here on purpose: the orchestrator includes these under the
existing ``catalog`` namespace, so the names resolve as ``catalog:submit_index``
and ``catalog:submit_entry``.
"""

from django.urls import path

from catalog import views_submit

urlpatterns = [
    path("", views_submit.submit_index, name="submit_index"),
    path("<str:kind>/", views_submit.submit_entry, name="submit_entry"),
]
