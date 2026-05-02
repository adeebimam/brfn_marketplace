from django.db import models
from django.contrib.auth.models import User
from apps.marketplace.models import Product

# Create your models here.
# from django.contrib.auth.models import User
from apps.marketplace.models import Product


class Cart(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.user.username}'s cart"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    def line_total(self):
        return self.product.calculate_price_for_quantity(self.quantity)["total"]

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"
