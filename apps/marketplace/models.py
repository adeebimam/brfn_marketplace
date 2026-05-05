from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta, date
from decimal import Decimal


MONTH_CHOICES = [
    (1, "January"), (2, "February"), (3, "March"),
    (4, "April"), (5, "May"), (6, "June"),
    (7, "July"), (8, "August"), (9, "September"),
    (10, "October"), (11, "November"), (12, "December"),
]

MONTH_NAMES = dict(MONTH_CHOICES)


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name


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

    UNIT_CHOICES = [
        ("each", "Each"),
        ("kg", "Kilogram"),
        ("g", "Gram"),
        ("dozen", "Dozen"),
        ("bunch", "Bunch"),
        ("litre", "Litre"),
        ("pack", "Pack"),
    ]

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
    unit = models.CharField(max_length=50, choices=UNIT_CHOICES, default="each")
    stock_quantity = models.PositiveIntegerField(default=0)

    low_stock_threshold = models.PositiveIntegerField(
        default=10,
        help_text="Send a low stock alert when stock falls below this number."
    )

    allergens = models.ManyToManyField(Allergen, blank=True)
    other_allergen_info = models.TextField(blank=True)

    harvest_date = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_organic = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    season = models.CharField(max_length=10, choices=SEASON_CHOICES, default="ALL")

    available_from_month = models.PositiveSmallIntegerField(
        choices=MONTH_CHOICES, null=True, blank=True,
        help_text="Month when this product comes into season.",
    )

    available_to_month = models.PositiveSmallIntegerField(
        choices=MONTH_CHOICES, null=True, blank=True,
        help_text="Last month this product is in season.",
    )

    image = models.ImageField(upload_to="product_images/", blank=True, null=True)

    # TC-019 Surplus Produce Fields
    is_surplus = models.BooleanField(default=False)

    surplus_discount_percent = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Discount percentage for surplus deals. Must be between 10 and 50."
    )

    surplus_discounted_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="Automatically calculated discounted price."
    )

    surplus_discount_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="Amount reduced from the normal price."
    )

    surplus_stock_quantity = models.PositiveIntegerField(
        default=0,
        help_text="Number of items available at discounted surplus price."
    )

    surplus_expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Date and time when the surplus deal expires."
    )

    surplus_note = models.TextField(blank=True, help_text="Optional note for customers about the surplus product.")

    best_before_date = models.DateField(null=True, blank=True, help_text="Best before date for surplus produce.")

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
        return month >= self.available_from_month or month <= self.available_to_month

    @property
    def season_label(self):
        if self.is_year_round:
            return "Available Year-Round"
        if self.available_from_month and self.available_to_month:
            return f"{MONTH_NAMES[self.available_from_month]} – {MONTH_NAMES[self.available_to_month]}"
        return self.get_season_display()

    # Stock alert helpers
    @property
    def is_low_stock(self):
        return self.stock_quantity < self.low_stock_threshold

    def check_low_stock(self):
        existing = StockNotification.objects.filter(product=self, is_resolved=False).first()
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

    # TC-019 Surplus helpers
    @property
    def is_active_surplus_deal(self):
        return (
            self.is_surplus
            and self.surplus_discount_percent
            and self.surplus_discounted_price > Decimal("0.00")
            and self.surplus_stock_quantity > 0
            and self.surplus_expires_at
            and self.surplus_expires_at > timezone.now()
            and self.stock_quantity > 0
        )

    @property
    def discounted_price(self):
        if self.is_active_surplus_deal:
            return self.surplus_discounted_price
        return self.price

    def calculate_price_for_quantity(self, quantity):
        quantity = int(quantity)
        if not self.is_active_surplus_deal:
            normal_total = (self.price * quantity).quantize(Decimal("0.01"))
            return {
                "discounted_qty": 0, "normal_qty": quantity,
                "discounted_unit_price": Decimal("0.00"), "normal_unit_price": self.price,
                "discounted_total": Decimal("0.00"), "normal_total": normal_total,
                "total": normal_total, "warning": "",
            }
        discounted_qty = min(quantity, self.surplus_stock_quantity)
        normal_qty = max(quantity - self.surplus_stock_quantity, 0)
        discounted_total = (self.surplus_discounted_price * discounted_qty).quantize(Decimal("0.01"))
        normal_total = (self.price * normal_qty).quantize(Decimal("0.01"))
        total = (discounted_total + normal_total).quantize(Decimal("0.01"))
        warning = ""
        if normal_qty > 0:
            warning = (
                f"Only {self.surplus_stock_quantity} item(s) are available at the discounted price. "
                f"The remaining {normal_qty} item(s) will be charged at the normal price."
            )
        return {
            "discounted_qty": discounted_qty, "normal_qty": normal_qty,
            "discounted_unit_price": self.surplus_discounted_price, "normal_unit_price": self.price,
            "discounted_total": discounted_total, "normal_total": normal_total,
            "total": total, "warning": warning,
        }

    def clean(self):
        super().clean()
        if self.season != "ALL":
            if self.available_from_month is None or self.available_to_month is None:
                return
        if (self.available_from_month is None) != (self.available_to_month is None):
            raise ValidationError("You must set both 'Available from' and 'Available to' months, or leave both blank.")
        if self.is_surplus:
            if self.surplus_discount_percent is None:
                raise ValidationError("Discount percentage is required for surplus items.")
            if not (10 <= self.surplus_discount_percent <= 50):
                raise ValidationError("Discount percentage must be between 10% and 50%.")
            if self.surplus_stock_quantity <= 0:
                raise ValidationError("Surplus stock quantity must be greater than 0.")
            if self.surplus_stock_quantity > self.stock_quantity:
                raise ValidationError("Surplus stock quantity cannot be more than total stock quantity.")
            if self.surplus_expires_at is None:
                raise ValidationError("Expiry date/time is required for surplus items.")
        else:
            self.surplus_discount_percent = None
            self.surplus_discounted_price = Decimal("0.00")
            self.surplus_discount_amount = Decimal("0.00")
            self.surplus_stock_quantity = 0
            self.surplus_expires_at = None
            self.surplus_note = ""
            self.best_before_date = None

    def save(self, *args, **kwargs):
        if self.is_surplus and self.surplus_discount_percent:
            discount_amount = (self.price * Decimal(self.surplus_discount_percent) / Decimal("100")).quantize(Decimal("0.01"))
            self.surplus_discount_amount = discount_amount
            self.surplus_discounted_price = (self.price - discount_amount).quantize(Decimal("0.01"))
        else:
            self.surplus_discount_amount = Decimal("0.00")
            self.surplus_discounted_price = Decimal("0.00")
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class StockNotification(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="stock_notifications")
    producer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="stock_notifications")
    message = models.CharField(max_length=255)
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.message


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        READY = "READY", "Ready"
        DELIVERED = "DELIVERED", "Delivered"
        CANCELLED = "CANCELLED", "Cancelled"

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders")
    created_at = models.DateTimeField(auto_now_add=True)
    delivery_address = models.CharField(max_length=255)
    delivery_postcode = models.CharField(max_length=20)
    special_instructions = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        return f"Order #{self.id}"


class ProducerOrder(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        READY = "READY", "Ready"
        DELIVERED = "DELIVERED", "Delivered"
        CANCELLED = "CANCELLED", "Cancelled"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="producer_orders")
    producer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="incoming_producer_orders")
    delivery_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    total_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["delivery_date", "id"]

    def clean(self):
        if self.order and self.delivery_date:
            order_date = self.order.created_at.date()
            minimum_delivery = order_date + timedelta(days=2)
            if self.delivery_date < minimum_delivery:
                raise ValidationError({"delivery_date": "Delivery date must be at least 48 hours after the order date."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Order #{self.order_id} -> {self.producer}"


class OrderItem(models.Model):
    producer_order = models.ForeignKey(ProducerOrder, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def line_total(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"


class ProducerOrderStatusHistory(models.Model):
    producer_order = models.ForeignKey(ProducerOrder, on_delete=models.CASCADE, related_name="status_history")
    old_status = models.CharField(max_length=20)
    new_status = models.CharField(max_length=20)
    note = models.TextField(blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self):
        return f"Order {self.producer_order_id}: {self.old_status} -> {self.new_status}"


class Review(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    rating = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=200)
    comment = models.TextField()
    anonymous = models.BooleanField(default=False)
    verified_purchase = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.rating is None:
            raise ValidationError("Rating is required.")
        if self.rating < 1 or self.rating > 5:
            raise ValidationError("Rating must be between 1 and 5.")

    def __str__(self):
        return f"{self.product.name} - {self.rating} stars"
class PurchaseReview(models.Model):
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="purchase_reviews"
    )

    order_number = models.CharField(max_length=50)

    rating = models.IntegerField()
    delivery_rating = models.IntegerField()
    packaging_rating = models.IntegerField()

    title = models.CharField(max_length=255)
    comment = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        for value in [self.rating, self.delivery_rating, self.packaging_rating]:
            if value is None or value < 1 or value > 5:
                raise ValidationError("Ratings must be between 1 and 5.")

    def __str__(self):
        return f"Purchase Review {self.order_number} - {self.rating} stars"
    


class CustomerOrderHistory(models.Model):
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer_order_history")
    order_number = models.CharField(max_length=50)
    order_data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.order_number