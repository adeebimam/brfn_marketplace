from django import forms
from django.forms.widgets import ClearableFileInput
from datetime import date, timedelta
from .models import Product, Allergen, ProducerOrder, MONTH_CHOICES

class NoClearableFileInput(forms.ClearableFileInput):
    template_name = 'widgets/no_clearable_file_input.html'

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

        # Friendly labels for seasonal fields
        self.fields["available_from_month"].label = "In season from"
        self.fields["available_to_month"].label = "In season to"

        # Make month fields optional with blank option
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
            "image",  # Add image field to form
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
            "unit": forms.Select(choices=Product.UNIT_CHOICES),
            "image": NoClearableFileInput,
        }

    def clean(self):
        cleaned_data = super().clean()

        season = cleaned_data.get("season")
        from_month = cleaned_data.get("available_from_month")
        to_month = cleaned_data.get("available_to_month")

        # If season is not ALL, require both month fields
        if season and season != "ALL":
            if not from_month or not to_month:
                raise forms.ValidationError(
                    "Please select both 'In season from' and 'In season to' months for seasonal products."
                )
        # If ALL, clear month fields
        if season == "ALL":
            cleaned_data["available_from_month"] = None
            cleaned_data["available_to_month"] = None

        # If one month is set the other must be too
        if (from_month and not to_month) or (to_month and not from_month):
            raise forms.ValidationError(
                "Please set both 'In season from' and 'In season to', or leave both blank for year-round."
            )

        return cleaned_data

    def save(self, commit=True):
        product = super().save(commit=False)
        # Invert: "Not available" checked → is_active = False
        product.is_active = not self.cleaned_data.get("not_available", False)
        if commit:
            product.save()
            self.save_m2m()
        return product


class CheckoutForm(forms.Form):
    delivery_address = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2})
    )
    delivery_postcode = forms.CharField(max_length=20)

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


class ProducerOrderStatusForm(forms.Form):
    status = forms.ChoiceField(choices=[])
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional note about status change"}),
    )

    def __init__(self, *args, **kwargs):
        status_choices = kwargs.pop("status_choices", ProducerOrder.Status.choices)
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = status_choices