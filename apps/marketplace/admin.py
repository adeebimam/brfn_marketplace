from django.contrib import admin
from django import forms
from .models import (
    Allergen, Category, Product,
    Order, ProducerOrder, OrderItem, ProducerOrderStatusHistory,
)


class ProductAdminForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = "__all__"
        widgets = {
            "allergens": forms.CheckboxSelectMultiple(),
        }
    def clean (self):
        cleaned_data = super().clean()
        category = cleaned_data.get("category")
        allergens = cleaned_data.get("allergens")
        other_info = cleaned_data.get("other_allergen_info")

        if category and category.is_food: 
            if not allergens and not other_info:
                raise forms.ValidationError ( 
                    "For food products, you must specify at least one allergen or provide other allergen information."
                )
        return cleaned_data 

class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product", "quantity", "unit_price")


class ProducerOrderInline(admin.TabularInline):
    model = ProducerOrder
    extra = 0
    readonly_fields = ("producer", "delivery_date", "status", "total_value")
    show_change_link = True


class StatusHistoryInline(admin.TabularInline):
    model = ProducerOrderStatusHistory
    extra = 0
    readonly_fields = ("old_status", "new_status", "note", "changed_by", "changed_at")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "created_at", "delivery_address")
    list_filter = ("created_at",)
    search_fields = ("customer__username", "delivery_address")
    inlines = [ProducerOrderInline]


@admin.register(ProducerOrder)
class ProducerOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "producer", "status", "delivery_date", "total_value")
    list_filter = ("status", "delivery_date")
    search_fields = ("producer__username", "order__id")
    list_editable = ("status",)
    inlines = [OrderItemInline, StatusHistoryInline]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("id", "producer_order", "product", "quantity", "unit_price")
    search_fields = ("product__name",)


@admin.register(ProducerOrderStatusHistory)
class ProducerOrderStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("producer_order", "old_status", "new_status", "changed_by", "changed_at")
    list_filter = ("new_status", "changed_at")
    readonly_fields = ("producer_order", "old_status", "new_status", "note", "changed_by", "changed_at")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_food")
    list_editable = ("is_food",)
    search_fields = ("name",)

admin.site.register(Allergen)
admin.site.register(Product, ProductAdmin)
