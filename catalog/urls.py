from django.urls import include, path

from catalog import views

app_name = "catalog"

urlpatterns = [
    path("", views.guitar_browse, name="browse"),
    path("guitars/<int:pk>/", views.guitar_detail, name="detail"),
    path("submit/", include("catalog.urls_submit")),
    path("review/", include("catalog.urls_review")),
]
