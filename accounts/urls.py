from django.urls import path

from accounts import views

# No app_name on purpose: these names sit in the same (unnamespaced) space as
# Django's built-in auth views (login/logout), so templates use {% url 'signup' %}
# and {% url 'login' %} side by side.
urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("confirm/<uuid:token>/", views.confirm_email, name="confirm_email"),
    path("resend-confirmation/", views.resend_confirmation, name="resend_confirmation"),
    path("theme/", views.set_theme, name="set_theme"),
]
