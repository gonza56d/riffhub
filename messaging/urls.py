from django.urls import path

from messaging import views

app_name = "messaging"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("u/<str:username>/", views.thread, name="thread"),
    path("u/<str:username>/send/", views.send, name="send"),
]
