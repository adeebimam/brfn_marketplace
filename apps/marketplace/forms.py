from django import forms
from datetime import date, timedelta
from .models import Product, Allergen, ProducerOrder

class ProductForm(forms.ModelForm):
    # Virtual field: ticking "Not available" sets is_active=False
    not_available = forms.BooleanField(required=False, label="Not available")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["allergens"].queryset = Allergen.objects.all()
        self.fields["allergens"].widget = forms.CheckboxSelectMultiple()
        self.fields["allergens"].help_text = "Tick all that apply. Leave all unticked if no allergens."
        # Pre-populate: if is_active is False, tick "Not available"
        if self.instance and self.instance.pk:
            self.fields["not_available"].initial = not self.instance.is_active

    class Meta:
        model = Product
        fields = ["category", 
                  "name", 
                  "description", 
                  "price", 
                  "stock_quantity", 
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

    def save(self, commit=True):
        product = super().save(commit=False)
        # Invert: "Not available" checked → is_active = False
        product.is_active = not self.cleaned_data.get("not_available", False)
        if commit:
            product.save()
            self.save_m2m()
        return product
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
