from django import forms
from datetime import date, timedelta


class CheckoutForm(forms.Form):

    delivery_address = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2})
    )

    delivery_date = forms.DateField(
    widget=forms.DateInput(
        attrs={
            "type": "date",
            "min": (date.today() + timedelta(days=2)).isoformat()
        }
    )
)
    

    PAYMENT_CHOICES = [
        ("stripe", "Stripe Test"),
        ("paypal", "PayPal Sandbox"),
    ]

    payment_method = forms.ChoiceField(choices=PAYMENT_CHOICES)

    card_number = forms.CharField(required=False)
    expiry = forms.CharField(required=False)
    cvc = forms.CharField(required=False)

    # 48 hour rule
    def clean_delivery_date(self):

        selected_date = self.cleaned_data["delivery_date"]

        minimum_date = date.today() + timedelta(days=2)

        if selected_date < minimum_date:
            raise forms.ValidationError(
                "Delivery must be at least 48 hours from now."
            )

        return selected_date