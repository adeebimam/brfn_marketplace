from django.contrib import admin
from django import forms
from .services import update_producer_order_status
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


class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm
    list_display = (
        "name",
        "producer",
        "price",
        "surplus_discount_percent",
        "surplus_discount_amount",
        "surplus_discounted_price",
        "surplus_stock_quantity",
        "surplus_expires_at",
        "is_surplus",
        "is_active",
    )
    list_filter = ("is_surplus", "is_active", "category")


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
    list_display = ("id", "customer", "status", "created_at", "delivery_address","delivery_postcode")
    list_filter = ("status","created_at",)
    search_fields = ("customer__username", "delivery_address")
    inlines = [ProducerOrderInline]


@admin.register(ProducerOrder)
class ProducerOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "producer", "status", "delivery_date", "total_value")
    list_filter = ("status", "delivery_date")
    search_fields = ("producer__username", "order__id")
    inlines = [OrderItemInline, StatusHistoryInline]
    def save_model(self, request, obj, form, change):
        if change:
            old_obj = ProducerOrder.objects.get(pk=obj.pk)
            new_status = form.cleaned_data.get("status")
            if old_obj.status != new_status:
                update_producer_order_status(
                        producer_order=old_obj,
                        new_status=new_status,
                        changed_by=request.user,
                        note="Updated via Django admin",
                        is_admin_override=True,
                    )
                return
            super().save_model(request, obj, form, change)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("id", "producer_order", "product", "quantity", "unit_price")
    search_fields = ("product__name",)


@admin.register(ProducerOrderStatusHistory)
class ProducerOrderStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("producer_order", "old_status", "new_status", "changed_by", "changed_at")
    list_filter = ("new_status", "changed_at")
    readonly_fields = ("producer_order", "old_status", "new_status", "note", "changed_by", "changed_at")


admin.site.register(Category)
admin.site.register(Allergen)
admin.site.register(Product, ProductAdmin)
