from django.urls import path

from messaging import views

app_name = "messaging"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("u/<str:username>/", views.thread, name="thread"),
    path("u/<str:username>/send/", views.send, name="send"),
    path("report/<int:message_id>/", views.report, name="report"),
    path("reports/", views.reports, name="reports"),
    path(
        "reports/<int:report_id>/<str:action>/",
        views.resolve_report,
        name="resolve_report",
    ),
]
