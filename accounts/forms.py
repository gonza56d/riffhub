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

    def clean_email(self) -> str:
        """Normalise the e-mail to lowercase and reject case-insensitive dupes.

        The model's ``unique`` constraint is enforced case-sensitively by
        Postgres, so without this two addresses that differ only in case (e.g.
        ``rt_user@example.com`` / ``RT_USER@EXAMPLE.COM``) would create distinct
        accounts. Lowercasing the whole address and checking ``email__iexact``
        keeps a single canonical account per address.
        """
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "A user with that e-mail already exists."
            )
        return email
