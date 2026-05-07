from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import Order, ProducerOrder, ProducerOrderStatusHistory, Product


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

    This keeps the stored product state aligned with what customers should see
    once a timed offer has ended.
    """
    if now is None:
        now = timezone.now()

    return Product.objects.filter(
        is_surplus=True,
        surplus_expires_at__isnull=False,
        surplus_expires_at__lte=now,
    ).update(
        is_surplus=False,
        surplus_discount_percent=None,
        surplus_discounted_price=Decimal("0.00"),
        surplus_discount_amount=Decimal("0.00"),
        surplus_stock_quantity=0,
        surplus_expires_at=None,
        surplus_note="",
        best_before_date=None,
    )


def recalculate_order_status(order):
    statuses = list(order.producer_orders.values_list("status", flat=True))

    if statuses and all(s == ProducerOrder.Status.CANCELLED for s in statuses):
        order.status = Order.Status.CANCELLED

    elif statuses and all(
        s in [ProducerOrder.Status.DELIVERED, ProducerOrder.Status.CANCELLED]
        for s in statuses
    ):
        order.status = Order.Status.COMPLETED

    elif any(
        s in [ProducerOrder.Status.CONFIRMED, ProducerOrder.Status.READY]
        for s in statuses
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
            raise ValueError(f"Invalid status progression: {old_status} -> {new_status}")

    with transaction.atomic():
        # Restore stock only when the order is being cancelled for the first time
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
