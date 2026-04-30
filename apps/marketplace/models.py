from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from datetime import timedelta


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name_plural = "Categories"

<<<<<<< Updated upstream
=======
    def __str__(self):
        return self.name


>>>>>>> Stashed changes
class Allergen(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Product(models.Model):
    SEASON_CHOICES = [
        ("SPRING", "Spring"),
        ("SUMMER", "Summer"),
        ("AUTUMN", "Autumn"),
        ("WINTER", "Winter"),
        ("ALL", "All Season"),
    ]

    producer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="products")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    price = models.DecimalField(max_digits=10, decimal_places=2)
<<<<<<< Updated upstream
    stock_quantity = models.PositiveIntegerField(default=0)
=======

    UNIT_CHOICES = [
        ("each", "Each"),
        ("kg", "Kilogram"),
        ("g", "Gram"),
        ("dozen", "Dozen"),
        ("bunch", "Bunch"),
        ("litre", "Litre"),
        ("pack", "Pack"),
    ]

    unit = models.CharField(max_length=50, choices=UNIT_CHOICES, default="each")
    stock_quantity = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(
        default=10,
        help_text="Send a low stock alert when stock falls below this number."
    )

>>>>>>> Stashed changes
    allergens = models.ManyToManyField(Allergen, blank=True)
    other_allergen_info = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    season = models.CharField(
        max_length=10,
        choices=SEASON_CHOICES,
        default="ALL"
    )

<<<<<<< Updated upstream
=======
    available_from_month = models.PositiveSmallIntegerField(
        choices=MONTH_CHOICES, null=True, blank=True,
        help_text="Month when this product comes into season (leave blank for year-round).",
    )
    available_to_month = models.PositiveSmallIntegerField(
        choices=MONTH_CHOICES, null=True, blank=True,
        help_text="Last month this product is in season (leave blank for year-round).",
    )

    @property
    def is_year_round(self):
        return (
            self.season == "ALL"
            or (self.available_from_month is None and self.available_to_month is None)
        )

    def is_in_season(self, ref_date=None):
        if self.is_year_round:
            return True
        if self.available_from_month is None or self.available_to_month is None:
            return True

        if ref_date is None:
            ref_date = date.today()
        month = ref_date.month

        if self.available_from_month <= self.available_to_month:
            return self.available_from_month <= month <= self.available_to_month
        else:
            return month >= self.available_from_month or month <= self.available_to_month

    @property
    def season_label(self):
        if self.is_year_round:
            return "Available Year-Round"
        if self.available_from_month and self.available_to_month:
            return f"{MONTH_NAMES[self.available_from_month]} – {MONTH_NAMES[self.available_to_month]}"
        return self.get_season_display()

    @property
    def is_low_stock(self):
        return self.stock_quantity < self.low_stock_threshold

    def check_low_stock(self):
        """
        Call this after stock is decremented.
        Creates a StockNotification if stock is below threshold.
        Resolves existing notification if stock is back above threshold.
        """
        existing = StockNotification.objects.filter(
            product=self,
            is_resolved=False
        ).first()

        if self.stock_quantity < self.low_stock_threshold:
            if not existing:
                StockNotification.objects.create(
                    product=self,
                    producer=self.producer,
                    message=f"Low Stock Alert: {self.name} - Only {self.stock_quantity} {self.unit} remaining",
                )
        else:
            if existing:
                existing.is_resolved = True
                existing.save()

    def clean(self):
        super().clean()
        if self.season != "ALL":
            if self.available_from_month is None or self.available_to_month is None:
                return
        if (self.available_from_month is None) != (self.available_to_month is None):
            raise ValidationError(
                "You must set both 'Available from' and 'Available to' months, or leave both blank."
            )
>>>>>>> Stashed changes

    def __str__(self):
        return self.name


class StockNotification(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="stock_notifications"
    )
    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="stock_notifications"
    )
    message = models.CharField(max_length=255)
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.message


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
        if self.order and self.delivery_date:
            order_date = self.order.created_at.date()
            minimum_delivery = order_date + timedelta(days=2)

            if self.delivery_date < minimum_delivery:
                raise ValidationError({
                    "delivery_date": "Delivery date must be at least 48 hours after the order date."
                })

    def save(self, *args, **kwargs):
        self.full_clean()
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


class ProducerOrderStatusHistory(models.Model):
    producer_order = models.ForeignKey(
        ProducerOrder,
        on_delete=models.CASCADE,
        related_name="status_history"
    )
    old_status = models.CharField(max_length=20)
    new_status = models.CharField(max_length=20)
    note = models.TextField(blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self):
        return f"Order {self.producer_order_id}: {self.old_status} -> {self.new_status}"