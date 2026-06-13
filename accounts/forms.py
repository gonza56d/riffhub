from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

User = get_user_model()


class SignUpForm(UserCreationForm):
    """Self-serve registration: username + unique e-mail + password (twice).

    Password strength is enforced by ``UserCreationForm`` via the project's
    AUTH_PASSWORD_VALIDATORS; e-mail uniqueness by the model's unique constraint.
    """

    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")
