from django.conf import settings
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from datetime import timedelta


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name


class Product(models.Model):
    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="products"
    )

    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock_quantity = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Order(models.Model):
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    delivery_address = models.CharField(max_length=255)
    delivery_postcode = models.CharField(max_length=20)

    special_instructions = models.TextField(blank=True)

    def __str__(self):
        return f"Order #{self.id}"


class ProducerOrder(models.Model):

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        READY = "READY", "Ready"
        DELIVERED = "DELIVERED", "Delivered"

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="producer_orders"
    )

    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="incoming_producer_orders"
    )

    delivery_date = models.DateField()

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )

    total_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    class Meta:
        ordering = ["delivery_date", "id"]

    def clean(self):
        """
        Enforce 48-hour minimum delivery time
        """
        if self.order and self.delivery_date:
            order_date = self.order.created_at.date()
            minimum_delivery = order_date + timedelta(days=2)

            if self.delivery_date < minimum_delivery:
                raise ValidationError({
                    "delivery_date":
                    "Delivery date must be at least 48 hours after the order date."
                })

    def save(self, *args, **kwargs):
        self.full_clean()  # ensure validation always runs
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Order #{self.order_id} -> {self.producer}"


class OrderItem(models.Model):

    producer_order = models.ForeignKey(
        ProducerOrder,
        on_delete=models.CASCADE,
        related_name="items"
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT
    )

    quantity = models.PositiveIntegerField(default=1)

    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    @property
    def line_total(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"
    