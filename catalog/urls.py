from django.urls import include, path

from catalog import views

app_name = "catalog"

urlpatterns = [
    path("", views.guitar_browse, name="browse"),
    path("guitars/<int:pk>/", views.guitar_detail, name="detail"),
    path("gear/<str:kind>/<int:pk>/", views.gear_detail, name="gear_detail"),
    path("comment/<str:kind>/<int:pk>/", views.add_catalog_comment, name="add_comment"),
    path("comment/<int:pk>/delete/", views.delete_catalog_comment, name="delete_comment"),
    path("submit/", include("catalog.urls_submit")),
    path("review/", include("catalog.urls_review")),
]
