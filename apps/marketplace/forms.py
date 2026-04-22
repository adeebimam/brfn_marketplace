from django import forms
from django.forms.widgets import ClearableFileInput
from django.db.models import Case, When, Value, IntegerField
from datetime import date, timedelta
from apps.accounts import models
from .models import Product, Allergen, ProducerOrder, MONTH_CHOICES, Review

class NoClearableFileInput(forms.ClearableFileInput):
    template_name = 'widgets/no_clearable_file_input.html'

class ProductForm(forms.ModelForm):
    not_available = forms.BooleanField(required=False, label="Not available")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["allergens"].queryset = Allergen.objects.all().order_by(
            Case(
                When(name="No common allergens", then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            "name"
        )

        self.fields["available_from_month"].label = "In season from"
        self.fields["available_to_month"].label = "In season to"
        self.fields["available_from_month"].required = False
        self.fields["available_to_month"].required = False
        self.fields["low_stock_threshold"].label = "Low stock alert threshold"
        self.fields["low_stock_threshold"].help_text = "You will be alerted when stock falls below this number."

    class Meta:
        model = Product
        fields = [
            "category",
            "name",
            "description",
            "price",
            "unit",
            "stock_quantity",
            "low_stock_threshold",
            "season",
            "available_from_month",
            "available_to_month",
            "allergens",
            "other_allergen_info",
            "harvest_date",
            "image",  # Add image field to form
            "is_organic",
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
            "low_stock_threshold": forms.NumberInput(attrs={"min": 0}),
        }

    def clean(self):
        cleaned_data = super().clean()
        allergens = cleaned_data.get("allergens")
        other_allergen_info = (cleaned_data.get("other_allergen_info") or "").strip()
        category = cleaned_data.get("category")
        cleaned_data["other_allergen_info"] = other_allergen_info

        if (not allergens or len(allergens) == 0) and not other_allergen_info:
            raise forms.ValidationError(
                "All products must declare allergen information. "
                "Select the allergens present, choose 'No common allergens' if none apply, "
                "or provide details in the other allergen info field."
            )
        season = cleaned_data.get("season")
        from_month = cleaned_data.get("available_from_month")
        to_month = cleaned_data.get("available_to_month")

        if season and season != "ALL":
            if not from_month or not to_month:
                raise forms.ValidationError(
                    "Please select both 'In season from' and 'In season to' months for seasonal products."
                )
        if season == "ALL":
            cleaned_data["available_from_month"] = None
            cleaned_data["available_to_month"] = None

        if (from_month and not to_month) or (to_month and not from_month):
            raise forms.ValidationError(
                "Please set both 'In season from' and 'In season to', or leave both blank for year-round."
            )

        return cleaned_data

    def save(self, commit=True):
        product = super().save(commit=False)
        product.is_active = not self.cleaned_data.get("not_available", False)
        if commit:
            product.save()
            self.save_m2m()
            # Check low stock after saving
            product.check_low_stock()
        return product


class CheckoutForm(forms.Form):
    delivery_address = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2})
    )
    delivery_postcode = forms.CharField(max_length=20)

    delivery_date = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "min": (date.today() + timedelta(days=2)).isoformat(),
            }
        )
    )


    PAYMENT_CHOICES = [
        ("stripe", "Stripe Test"),
        ("paypal", "PayPal Sandbox"),
    ]

    payment_method = forms.ChoiceField(choices=PAYMENT_CHOICES)

    card_number = forms.CharField(required=False)
    expiry = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "MM/YY",
            "pattern": r"^(0[1-9]|1[0-2])\/\d{2}$"
        })
    )
    cvc = forms.CharField(required=False)

    def clean_delivery_date(self):
        selected_date = self.cleaned_data.get("delivery_date")

        if not selected_date:
            return selected_date

        minimum_date = date.today() + timedelta(days=2)

        if selected_date < minimum_date:
            raise forms.ValidationError(
                "Delivery must be at least 48 hours from now."
            )

        return selected_date

    def clean_expiry(self):
        expiry = self.cleaned_data.get("expiry", "").strip()

        if not expiry:
            return expiry

        if len(expiry) != 5 or expiry[2] != "/":
            raise forms.ValidationError("Enter expiry date in MM/YY format.")

        month_part, year_part = expiry.split("/")

        if not (month_part.isdigit() and year_part.isdigit()):
            raise forms.ValidationError("Enter expiry date in MM/YY format.")

        month = int(month_part)
        year = int(year_part)

        if month < 1 or month > 12:
            raise forms.ValidationError("Enter a valid month.")

        today = date.today()
        current_month = today.month
        current_year = today.year % 100

        if year < current_year or (year == current_year and month < current_month):
            raise forms.ValidationError("Card expiry date cannot be in the past.")

        return expiry


class ProducerOrderStatusForm(forms.Form):
    status = forms.ChoiceField(choices=[])
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional note about status change"}),
    )

class ReviewForm(forms.ModelForm):
    rating = forms.IntegerField(
        min_value=1,
        max_value=5,
        required=True,
        widget=forms.NumberInput(attrs={
            "min": 1,
            "max": 5,
            "placeholder": "1-5"
        })
    )

    class Meta:
        model = Review
        fields = ["rating", "title", "comment", "anonymous"]
        widgets = {
            "title": forms.TextInput(attrs={
                "placeholder": "Review title"
            }),
            "comment": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": "Write your review here..."
            }),
        }

    def clean_rating(self):
        rating = self.cleaned_data.get("rating")

        if rating is None:
            raise forms.ValidationError("Rating is required.")

        if rating < 1 or rating > 5:
            raise forms.ValidationError("Rating must be between 1 and 5.")

        return rating


   
