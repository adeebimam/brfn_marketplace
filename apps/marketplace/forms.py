from django import forms
from .models import Product

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["category", "name", "description", "price", "unit", "stock_quantity", "allergens", "harvest_date", "in_season"]
