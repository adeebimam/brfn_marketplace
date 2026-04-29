from django.db import transaction

from .models import Order, ProducerOrder, ProducerOrderStatusHistory


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