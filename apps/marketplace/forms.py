from django import forms
from .models import Product, ProducerOrder


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "category",
            "name",
            "description",
            "price",
            "stock_quantity",
            "is_active",
        ]


class ProducerOrderStatusForm(forms.Form):
    status = forms.ChoiceField(
        choices=ProducerOrder.Status.choices
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3})
    )