from django.urls import path

from moderation import views

app_name = "moderation"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("warn/<int:pk>/", views.warn_user, name="warn_user"),
    path("silence/<int:pk>/", views.silence_user, name="silence_user"),
    path("ban/<int:pk>/", views.ban_user, name="ban_user"),
    path("lift-ban/<int:pk>/", views.lift_ban_user, name="lift_ban_user"),
    path("move/<int:pk>/", views.move_post, name="move_post"),
    path("remove/<str:kind>/<int:pk>/", views.remove_content, name="remove_content"),
    path("restore/<str:kind>/<int:pk>/", views.restore_content, name="restore_content"),
]
