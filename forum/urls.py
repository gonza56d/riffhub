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
    # Creator-only topic / subtopic management
    path("manage/", views.manage_topics, name="manage_topics"),
    path("manage/topic/new/", views.topic_create, name="topic_create"),
    path("manage/topic/<int:pk>/edit/", views.topic_edit, name="topic_edit"),
    path("manage/topic/<int:pk>/delete/", views.topic_delete, name="topic_delete"),
    path("manage/topic/<int:pk>/subtopic/new/", views.subtopic_create, name="subtopic_create"),
    path("manage/subtopic/<int:pk>/edit/", views.subtopic_edit, name="subtopic_edit"),
    path("manage/subtopic/<int:pk>/delete/", views.subtopic_delete, name="subtopic_delete"),
    # Generic engagement endpoints (kept last; they match <str>/<int>/… shapes)
    path("<str:target>/<int:pk>/vote/<str:value>/", views.vote, name="vote"),
    path("<str:target>/<int:pk>/react/", views.react, name="react"),
]
