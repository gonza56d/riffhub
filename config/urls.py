from django.contrib import admin
from django.urls import include, path

from accounts import views as account_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounts/", include("accounts.urls")),
    path("forum/", include("forum.urls")),
    path("moderation/", include("moderation.urls")),
    path("u/<str:username>/", account_views.profile, name="profile"),
    path("", include("catalog.urls")),
]
