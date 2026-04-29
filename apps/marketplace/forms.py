from django import forms
from datetime import date, timedelta
from .models import Product, Allergen, ProducerOrder


class ProductForm(forms.ModelForm):
    # Virtual field: ticking "Not available" sets is_active=False
    not_available = forms.BooleanField(required=False, label="Not available")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Pre-populate: if is_active is False, tick "Not available"
        if self.instance and self.instance.pk:
            self.fields["not_available"].initial = not self.instance.is_active

        self.fields["allergens"].queryset = Allergen.objects.all()
        self.fields["allergens"].help_text = "Tick all that apply. Leave all unticked if no allergens."

        # Friendly labels
        self.fields["available_from_month"].label = "In season from"
        self.fields["available_to_month"].label = "In season to"

        self.fields["available_from_month"].required = False
        self.fields["available_to_month"].required = False

    class Meta:
        model = Product
        fields = [
            "category",
            "name",
            "description",
            "price",
            "unit",
            "stock_quantity",
            "season",
            "available_from_month",
            "available_to_month",
            "allergens",
            "other_allergen_info",
            "harvest_date",

            # ✅ TC-019 fields
            "is_surplus",
            "surplus_discount_percent",
            "surplus_expires_at",
            "surplus_note",
            "best_before_date",
        ]

        widgets = {
            "allergens": forms.CheckboxSelectMultiple(),
            "other_allergen_info": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Provide details about any other allergens not listed above.",
                }
            ),
            "harvest_date": forms.DateInput(attrs={"type": "date"}),

            # ✅ better inputs for surplus fields
            "surplus_expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "best_before_date": forms.DateInput(attrs={"type": "date"}),

            "unit": forms.Select(choices=Product.UNIT_CHOICES),
        }

    def clean(self):
        cleaned_data = super().clean()

        season = cleaned_data.get("season")
        from_month = cleaned_data.get("available_from_month")
        to_month = cleaned_data.get("available_to_month")

        # Seasonal validation
        if season and season != "ALL":
            if not from_month or not to_month:
                raise forms.ValidationError(
                    "Please select both 'In season from' and 'In season to' months."
                )

        if season == "ALL":
            cleaned_data["available_from_month"] = None
            cleaned_data["available_to_month"] = None

        if (from_month and not to_month) or (to_month and not from_month):
            raise forms.ValidationError(
                "Set both seasonal months or leave both blank."
            )

        # ✅ TC-019 Surplus validation
        is_surplus = cleaned_data.get("is_surplus")
        discount = cleaned_data.get("surplus_discount_percent")
        expiry = cleaned_data.get("surplus_expires_at")

        if is_surplus:
            if discount is None:
                raise forms.ValidationError("Discount is required for surplus products.")

            if not (10 <= discount <= 50):
                raise forms.ValidationError("Discount must be between 10% and 50%.")

            if expiry is None:
                raise forms.ValidationError("Expiry date/time is required for surplus products.")

        return cleaned_data

    def save(self, commit=True):
        product = super().save(commit=False)

        # Handle availability toggle
        product.is_active = not self.cleaned_data.get("not_available", False)

        if commit:
            product.save()
            self.save_m2m()

        return product


# -----------------------------
# Checkout
# -----------------------------
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

    def clean_delivery_date(self):
        selected_date = self.cleaned_data["delivery_date"]
        minimum_date = date.today() + timedelta(days=2)

        if selected_date < minimum_date:
            raise forms.ValidationError(
                "Delivery must be at least 48 hours from now."
            )

        return selected_date


# -----------------------------
# Order Status Update
# -----------------------------
class ProducerOrderStatusForm(forms.Form):
    status = forms.ChoiceField(choices=ProducerOrder.Status.choices)
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"rows": 3, "placeholder": "Optional note about status change"}
        ),
    )