from django.contrib import admin
from .models import Category, Product, Order, ProducerOrder, OrderItem

admin.site.register(Category)
admin.site.register(Product)
admin.site.register(Order)
admin.site.register(ProducerOrder)
admin.site.register(OrderItem)