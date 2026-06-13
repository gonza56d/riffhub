from django.urls import path

from catalog import views

app_name = "catalog"

urlpatterns = [
    path("", views.guitar_browse, name="browse"),
    path("guitars/<int:pk>/", views.guitar_detail, name="detail"),
]
