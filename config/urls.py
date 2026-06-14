from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve as serve_media

from accounts import views as account_views

urlpatterns = [
    path("admin/", admin.site.urls),
    # Serve user-uploaded media from the app. In dev this is runserver; in prod
    # (e.g. Render, which has no separate media server) Django serves it too,
    # backed by a persistent disk mounted at MEDIA_ROOT. For scale, move media
    # to S3-compatible object storage (django-storages) — see PRODUCT.md.
    re_path(
        r"^media/(?P<path>.*)$",
        serve_media,
        {"document_root": settings.MEDIA_ROOT},
    ),
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounts/", include("accounts.urls")),
    path("forum/", include("forum.urls")),
    # Moderator-only audit of author-deleted posts. PRODUCT.md puts it at the
    # site root (``{root_url}/deleted``), though it's served by the forum app.
    path("deleted/", include("forum.deleted_urls")),
    path("moderation/", include("moderation.urls")),
    path("messages/", include("messaging.urls")),
    path("u/<str:username>/", account_views.profile, name="profile"),
    path("", include("catalog.urls")),
]
