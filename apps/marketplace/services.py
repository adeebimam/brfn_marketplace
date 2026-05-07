from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import (
    Order,
    ProducerOrder,
    ProducerOrderStatusHistory,
    Product,
    SurplusAnalyticsRecord,
)


ALLOWED_TRANSITIONS = {
    ProducerOrder.Status.PENDING: [
        ProducerOrder.Status.CONFIRMED,
        ProducerOrder.Status.CANCELLED,
    ],
    ProducerOrder.Status.CONFIRMED: [
        ProducerOrder.Status.READY,
        ProducerOrder.Status.CANCELLED,
    ],
    ProducerOrder.Status.READY: [
        ProducerOrder.Status.DELIVERED,
    ],
    ProducerOrder.Status.DELIVERED: [],
    ProducerOrder.Status.CANCELLED: [],
}


def expire_surplus_deals(now=None):
    """
    Clear expired surplus pricing from the database.
    """
    if now is None:
        now = timezone.now()

    expired_products = list(
        Product.objects.filter(
            is_surplus=True,
            surplus_expires_at__isnull=False,
            surplus_expires_at__lte=now,
        )
    )

    if not expired_products:
        return 0

    analytics_records = []

    for product in expired_products:
        unsold_quantity = int(product.surplus_stock_quantity or 0)
        if unsold_quantity > 0:
            analytics_records.append(
                SurplusAnalyticsRecord(
                    producer=product.producer,
                    product=product,
                    record_type=SurplusAnalyticsRecord.RecordType.UNSOLD,
                    quantity=unsold_quantity,
                    estimated_weight_kg=(
                        product.estimated_unit_weight_kg * Decimal(unsold_quantity)
                    ).quantize(Decimal("0.01")),
                    customer_saving=Decimal("0.00"),
                    revenue=Decimal("0.00"),
                )
            )

        product.is_surplus = False
        product.surplus_discount_percent = None
        product.surplus_discounted_price = Decimal("0.00")
        product.surplus_discount_amount = Decimal("0.00")
        product.surplus_stock_quantity = 0
        product.surplus_expires_at = None
        product.surplus_note = ""
        product.best_before_date = None

    with transaction.atomic():
        if analytics_records:
            SurplusAnalyticsRecord.objects.bulk_create(analytics_records)
        Product.objects.bulk_update(
            expired_products,
            [
                "is_surplus",
                "surplus_discount_percent",
                "surplus_discounted_price",
                "surplus_discount_amount",
                "surplus_stock_quantity",
                "surplus_expires_at",
                "surplus_note",
                "best_before_date",
            ],
        )

    return len(expired_products)


def notify_favourite_customers_about_surplus(product):
    """
    Create in-app notifications for customers who favourited this producer
    when the producer creates/updates an active surplus deal.
    """
    from .models import FavouriteProducer, SurplusDealNotification

    if not product.is_active_surplus_deal:
        return 0

    favourites = FavouriteProducer.objects.filter(
        producer=product.producer
    ).select_related("customer")

    created_count = 0

    for favourite in favourites:
        notification, created = SurplusDealNotification.objects.get_or_create(
            customer=favourite.customer,
            producer=product.producer,
            product=product,
            defaults={
                "message": (
                    f"New surplus deal from {product.producer.username}: "
                    f"{product.name} is now available at a discounted price."
                )
            },
        )

        if created:
            created_count += 1
        else:
            # If the same product becomes a surplus deal again, make the old notification unread again.
            notification.message = (
                f"Updated surplus deal from {product.producer.username}: "
                f"{product.name} is available at a discounted price."
            )
            notification.is_read = False
            notification.save(update_fields=["message", "is_read"])

    return created_count


def recalculate_order_status(order):
    statuses = list(order.producer_orders.values_list("status", flat=True))

    if statuses and all(
        status == ProducerOrder.Status.CANCELLED
        for status in statuses
    ):
        order.status = Order.Status.CANCELLED

    elif statuses and all(
        status in [
            ProducerOrder.Status.DELIVERED,
            ProducerOrder.Status.CANCELLED,
        ]
        for status in statuses
    ):
        order.status = Order.Status.DELIVERED

    elif any(
        status in [
            ProducerOrder.Status.CONFIRMED,
            ProducerOrder.Status.READY,
        ]
        for status in statuses
    ):
        order.status = Order.Status.READY

    else:
        order.status = Order.Status.PENDING

    order.save(update_fields=["status"])


def restore_stock_for_cancelled_order(producer_order):
    """
    Restore stock when a producer order is cancelled.
    This should only run once, when moving from a non-cancelled status to CANCELLED.
    """
    items = producer_order.items.select_related("product")

    for item in items:
        product = item.product
        product.stock_quantity += item.quantity
        product.save(update_fields=["stock_quantity"])


def update_producer_order_status(
    producer_order,
    new_status,
    changed_by,
    note="",
    is_admin_override=False,
):
    old_status = producer_order.status

    if old_status == new_status:
        return False

    if not is_admin_override:
        if new_status not in ALLOWED_TRANSITIONS.get(old_status, []):
            raise ValueError(
                f"Invalid status progression: {old_status} -> {new_status}"
            )

    with transaction.atomic():
        if (
            new_status == ProducerOrder.Status.CANCELLED
            and old_status != ProducerOrder.Status.CANCELLED
        ):
            restore_stock_for_cancelled_order(producer_order)

        producer_order.status = new_status
        producer_order.save(update_fields=["status"])

        ProducerOrderStatusHistory.objects.create(
            producer_order=producer_order,
            old_status=old_status,
            new_status=new_status,
            note=note,
            changed_by=changed_by,
        )

        recalculate_order_status(producer_order.order)

    return True
