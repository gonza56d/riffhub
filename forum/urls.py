from django.urls import path

from forum import views

app_name = "forum"

urlpatterns = [
    path("", views.index, name="index"),
    path("s/<int:pk>/", views.subtopic_detail, name="subtopic"),
    path("s/<int:pk>/post/", views.post_create, name="post_create"),
    path("s/<int:pk>/disclaimer/", views.accept_disclaimer, name="accept_disclaimer"),
    path("post/<int:pk>/", views.post_detail, name="post"),
    path("post/<int:pk>/comment/", views.comment_create, name="comment_create"),
    path("<str:target>/<int:pk>/vote/<str:value>/", views.vote, name="vote"),
    path("<str:target>/<int:pk>/react/", views.react, name="react"),
]
