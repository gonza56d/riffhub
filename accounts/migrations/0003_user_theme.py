from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_user_accepted_submissions_count_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="theme",
            field=models.CharField(
                choices=[("light", "Light"), ("dark", "Dark")],
                default="light",
                help_text="Preferred colour theme for the site UI (light or dark).",
                max_length=5,
            ),
        ),
    ]
