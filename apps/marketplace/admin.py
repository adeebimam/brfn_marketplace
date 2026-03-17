from django.contrib import admin
from django import forms
from .models import Allergen, Category, Product


class ProductAdminForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = "__all__"
        widgets = {
            "allergens": forms.CheckboxSelectMultiple(),
        }


class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm


admin.site.register(Category)
admin.site.register(Allergen)
admin.site.register(Product, ProductAdmin)