from django import forms

from forum.models import Subtopic, Topic


class TopicForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = ["name", "description", "is_market", "requires_disclaimer"]


class SubtopicForm(forms.ModelForm):
    class Meta:
        model = Subtopic
        fields = ["topic", "name"]
