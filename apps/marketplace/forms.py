from django import forms
from .models import Product

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["category", 
                  "name", 
                  "description", 
                  "price", 
                  "stock_quantity", 
                  "is_active",
                  "season",
                  ]
        widgets = {
            "allergens": forms.CheckboxSelectMultiple(),
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
