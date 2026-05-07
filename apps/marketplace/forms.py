from datetime import date, timedelta

from django import forms
from django.db.models import Case, When, Value, IntegerField
from django.utils import timezone

from apps.accounts import models
from .models import Product, Allergen, ProducerOrder, MONTH_CHOICES, Review, PurchaseReview


class NoClearableFileInput(forms.ClearableFileInput):
    template_name = "widgets/no_clearable_file_input.html"


class ProductForm(forms.ModelForm):
    not_available = forms.BooleanField(required=False, label="Not available")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            self.fields["not_available"].initial = not self.instance.is_active

        self.fields["allergens"].queryset = Allergen.objects.all().order_by(
            Case(
                When(name="No common allergens", then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            "name"
        )
        self.fields["allergens"].help_text = "Tick all that apply. Leave all unticked if no allergens."

        self.fields["available_from_month"].label = "In season from"
        self.fields["available_to_month"].label = "In season to"
        self.fields["available_from_month"].required = False
        self.fields["available_to_month"].required = False

        self.fields["unit"].required = False
        self.fields["unit"].initial = Product._meta.get_field("unit").default

        self.fields["low_stock_threshold"].label = "Low stock alert threshold"
        self.fields["low_stock_threshold"].help_text = "You will be alerted when stock falls below this number."

        self.fields["surplus_stock_quantity"].required = False

        current_local_dt = timezone.localtime()
        self.fields["surplus_expires_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["surplus_expires_at"].widget.attrs["min"] = current_local_dt.strftime("%Y-%m-%dT%H:%M")
        self.fields["best_before_date"].widget.attrs["min"] = timezone.localdate().isoformat()

        if self.instance.pk and self.instance.surplus_expires_at:
            self.initial["surplus_expires_at"] = (
                timezone.localtime(self.instance.surplus_expires_at).strftime("%Y-%m-%dT%H:%M")
            )

    class Meta:
        model = Product
        fields = [
            "category",
            "name",
            "description",
            "price",
            "unit",
            "stock_quantity",
            "estimated_unit_weight_kg",
            "low_stock_threshold",
            "season",
            "available_from_month",
            "available_to_month",
            "allergens",
            "other_allergen_info",
            "harvest_date",
            "image",
            "is_organic",
            "is_surplus",
            "surplus_discount_percent",
            "surplus_stock_quantity",
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
            "surplus_expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "best_before_date": forms.DateInput(attrs={"type": "date"}),
            "unit": forms.Select(choices=Product.UNIT_CHOICES),
            "image": NoClearableFileInput(),
            "low_stock_threshold": forms.NumberInput(attrs={"min": 0}),
            "estimated_unit_weight_kg": forms.NumberInput(attrs={"min": 0, "step": "0.01"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        allergens = cleaned_data.get("allergens")
        other_allergen_info = (cleaned_data.get("other_allergen_info") or "").strip()
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

        if not cleaned_data.get("unit"):
            cleaned_data["unit"] = Product._meta.get_field("unit").default

        if season and season != "ALL":
            if not from_month or not to_month:
                raise forms.ValidationError(
                    "Please select both 'In season from' and 'In season to' months."
                )

        if season == "ALL":
            cleaned_data["available_from_month"] = None
            cleaned_data["available_to_month"] = None

        if (from_month and not to_month) or (to_month and not from_month):
            raise forms.ValidationError("Set both seasonal months or leave both blank.")

        is_surplus = cleaned_data.get("is_surplus")
        discount = cleaned_data.get("surplus_discount_percent")
        surplus_stock_quantity = cleaned_data.get("surplus_stock_quantity")
        expiry = cleaned_data.get("surplus_expires_at")
        stock_quantity = cleaned_data.get("stock_quantity")

        if is_surplus:
            if discount is None:
                raise forms.ValidationError("Discount is required for surplus products.")
            if not 10 <= discount <= 50:
                raise forms.ValidationError("Discount must be between 10% and 50%.")
            if not surplus_stock_quantity:
                raise forms.ValidationError("Surplus stock quantity is required for surplus products.")
            if stock_quantity is not None and surplus_stock_quantity > stock_quantity:
                raise forms.ValidationError("Surplus stock quantity cannot be more than total stock quantity.")
            if expiry is None:
                raise forms.ValidationError("Expiry date/time is required for surplus products.")
            if expiry <= timezone.now():
                raise forms.ValidationError("Expiry date/time must be today or in the future.")
        else:
            cleaned_data["surplus_stock_quantity"] = 0

        return cleaned_data

    def save(self, commit=True):
        product = super().save(commit=False)
        product.is_active = not self.cleaned_data.get("not_available", False)
        if commit:
            product.save()
            self.save_m2m()
            product.check_low_stock()
        return product


# -----------------------------
# Checkout
# -----------------------------

class CheckoutForm(forms.Form):
    delivery_address = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Enter your delivery address"})
    )

    delivery_postcode = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={"placeholder": "Enter postcode"})
    )

    delivery_date = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={"type": "date", "min": (date.today() + timedelta(days=2)).isoformat()}
        ),
    )

    PAYMENT_CHOICES = [
        ("stripe", "Stripe Test"),
        ("paypal_sandbox", "PayPal Sandbox"),
    ]

    payment_method = forms.ChoiceField(choices=PAYMENT_CHOICES)
    card_number = forms.CharField(required=False)
    expiry = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "MM/YY", "pattern": r"^(0[1-9]|1[0-2])\/\d{2}$"}),
    )
    cvc = forms.CharField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["delivery_date"].widget.attrs["min"] = (
            timezone.localdate() + timedelta(days=2)
        ).isoformat()

    def clean_delivery_date(self):
        selected_date = self.cleaned_data.get("delivery_date")
        if not selected_date:
            return selected_date
        minimum_date = timezone.localdate() + timedelta(days=2)
        if selected_date < minimum_date:
            raise forms.ValidationError("Delivery must be at least 48 hours from now.")
        return selected_date

    def clean_expiry(self):
        expiry = self.cleaned_data.get("expiry", "").strip()
        if not expiry:
            return expiry
        if len(expiry) != 5 or expiry[2] != "/":
            raise forms.ValidationError("Enter expiry date in MM/YY format.")
        month_part, year_part = expiry.split("/")
        if not month_part.isdigit() or not year_part.isdigit():
            raise forms.ValidationError("Enter expiry date in MM/YY format.")
        month = int(month_part)
        year = int(year_part)
        if month < 1 or month > 12:
            raise forms.ValidationError("Enter a valid month.")
        today = timezone.localdate()
        current_year = today.year % 100
        if year < current_year or (year == current_year and month < today.month):
            raise forms.ValidationError("Card expiry date cannot be in the past.")
        return expiry


# -----------------------------
# Order Status Update
# -----------------------------

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


# -----------------------------
# Reviews
# -----------------------------

class ReviewForm(forms.ModelForm):
    rating = forms.IntegerField(
        min_value=1,
        max_value=5,
        required=True,
        widget=forms.NumberInput(attrs={"min": 1, "max": 5, "placeholder": "1-5"}),
    )

    class Meta:
        model = Review
        fields = ["rating", "title", "comment", "anonymous"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Review title"}),
            "comment": forms.Textarea(attrs={"rows": 4, "placeholder": "Write your review here..."}),
        }

    def clean_rating(self):
        rating = self.cleaned_data.get("rating")
        if rating is None:
            raise forms.ValidationError("Rating is required.")
        if rating < 1 or rating > 5:
            raise forms.ValidationError("Rating must be between 1 and 5.")
        return rating


class PurchaseReviewForm(forms.ModelForm):
    rating = forms.IntegerField(min_value=1, max_value=5)
    delivery_rating = forms.IntegerField(min_value=1, max_value=5)
    packaging_rating = forms.IntegerField(min_value=1, max_value=5)

    class Meta:
        model = PurchaseReview
        fields = ["rating", "delivery_rating", "packaging_rating", "title", "comment"]
        widgets = {
            "comment": forms.Textarea(attrs={"rows": 4}),
        }
