"""Root-mounted ``/deleted`` audit area (moderators only).

Kept separate from ``forum.urls`` so PRODUCT.md's ``{root_url}/deleted`` lives at
the site root (mounted in ``config.urls``) while still being served by the forum
app. Reverse with the ``deleted`` namespace, e.g. ``deleted:index``.
"""

from django.urls import path

from forum import views

app_name = "deleted"

urlpatterns = [
    path("", views.deleted_index, name="index"),
    path("s/<int:pk>/", views.deleted_subtopic, name="subtopic"),
]
