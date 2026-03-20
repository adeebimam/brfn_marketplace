from django import forms
from datetime import date, timedelta
from .models import Product, Allergen, ProducerOrder

class ProductForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["allergens"].queryset = Allergen.objects.all()
        self.fields["allergens"].widget = forms.CheckboxSelectMultiple()
        self.fields["allergens"].help_text = "Tick all that apply. Leave all unticked if no allergens."
    class Meta:
        model = Product
        fields = ["category", 
                  "name", 
                  "description", 
                  "price", 
                  "stock_quantity", 
                  "is_active",
                  "season",
                  "allergens",  # Added allergens field
                  "other_allergen_info",  # Added other allergen info field
                  ]
        widgets = {
            # 'allergens' widget is overridden in __init__
            "other_allergen_info": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Provide details about any other allergens not listed above.",
                }
            ),
        }
    def clean(self):
        cleaned_data = super().clean()
        allergens = cleaned_data.get("allergens")
        other_allergen_info = cleaned_data.get("other_allergen_info")
        category = cleaned_data.get("category")

        if category and category.name.lower() in ["bakery", "dairy", "food", "produce"]:
            if (not allergens or len(allergens) == 0) and not other_allergen_info:
                raise forms.ValidationError(
                    "Allergen information must be provided for food products."
                )

        return cleaned_data

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


class ProducerOrderStatusForm(forms.Form):
    status = forms.ChoiceField(choices=ProducerOrder.Status.choices)
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional note about status change"}),
    )
