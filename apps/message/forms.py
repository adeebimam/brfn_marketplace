from django import forms
from .models import Message


class StartMessageForm(forms.Form):
    recipient = forms.ModelChoiceField(
        queryset=None,
        label="Recipient"
    )

    subject = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "placeholder": "Subject"
        })
    )

    body = forms.CharField(
        label="Message",
        widget=forms.Textarea(attrs={
            "placeholder": "Write your message...",
            "rows": 5
        })
    )

    def __init__(self, *args, allowed_recipients=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recipient"].queryset = allowed_recipients


class ReplyForm(forms.ModelForm):
    class Meta:
        model = Message
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(attrs={
                "placeholder": "Write your reply...",
                "rows": 4
            })
        }