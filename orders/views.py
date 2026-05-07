from collections import defaultdict
from decimal import Decimal
from datetime import datetime
from django.utils import timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import render, redirect
from apps.marketplace.models import CustomerOrderHistory
from apps.marketplace.models import CommissionLog

from apps.cart.models import Cart
from apps.marketplace.models import (
    Order,
    ProducerOrder,
    OrderItem,
    Product,
    ProducerOrderStatusHistory,
    RecurringOrder,
)
from apps.marketplace.services import expire_surplus_deals


@login_required
def checkout(request):
    return render(request, "orders/checkout.html")


def _build_priced_cart_item(cart_item):
    pricing = cart_item.product.calculate_price_for_quantity(cart_item.quantity)

    return {
        "cart_item": cart_item,
        "product": cart_item.product,
        "qty": cart_item.quantity,
        "line_total": pricing["total"],
        **pricing,
    }


def _build_order_item_entries(product, quantity):
    pricing = product.calculate_price_for_quantity(quantity)
    entries = []

    if pricing["discounted_qty"] > 0:
        entries.append({
            "product": product,
            "quantity": pricing["discounted_qty"],
            "unit_price": pricing["discounted_unit_price"],
            "line_total": pricing["discounted_total"],
        })

    if pricing["normal_qty"] > 0:
        entries.append({
            "product": product,
            "quantity": pricing["normal_qty"],
            "unit_price": pricing["normal_unit_price"],
            "line_total": pricing["normal_total"],
        })

    return pricing, entries


def _update_product_inventory(product, quantity, discounted_qty):
    product.stock_quantity = max(product.stock_quantity - quantity, 0)

    if product.is_surplus:
        product.surplus_stock_quantity = max(
            0,
            product.surplus_stock_quantity - discounted_qty,
        )

        if product.surplus_stock_quantity == 0 or product.stock_quantity == 0:
            product.is_surplus = False
            product.surplus_discount_percent = None
            product.surplus_discounted_price = Decimal("0.00")
            product.surplus_discount_amount = Decimal("0.00")
            product.surplus_stock_quantity = 0
            product.surplus_expires_at = None
            product.surplus_note = ""
            product.best_before_date = None
        elif product.surplus_stock_quantity > product.stock_quantity:
            product.surplus_stock_quantity = product.stock_quantity

    product.save()


def _order_total(order):
    return sum(
        (producer_order.total_value for producer_order in order.producer_orders.all()),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))


def _as_money(value):
    if value in (None, ""):
        return Decimal("0.00")

    return Decimal(str(value)).quantize(Decimal("0.01"))


def _order_status_steps(order):
    producer_orders = list(order.producer_orders.all())
    producer_statuses = {producer_order.status for producer_order in producer_orders}

    confirmed = any(
        status in {
            ProducerOrder.Status.CONFIRMED,
            ProducerOrder.Status.READY,
            ProducerOrder.Status.DELIVERED,
        }
        for status in producer_statuses
    )

    ready = any(
        status in {
            ProducerOrder.Status.READY,
            ProducerOrder.Status.DELIVERED,
        }
        for status in producer_statuses
    )

    delivered = order.status == Order.Status.COMPLETED

    steps = [
        {"label": "Order placed", "complete": True},
        {"label": "Producer confirmed", "complete": confirmed},
        {"label": "Ready for delivery", "complete": ready},
        {"label": "Delivered", "complete": delivered},
    ]

    if order.status == Order.Status.CANCELLED:
        return steps, "cancelled"

    current_index = next(
        (index for index, step in enumerate(steps) if not step["complete"]),
        len(steps) - 1,
    )

    return steps, current_index


@login_required
def payment(request):
    expire_surplus_deals()

    total = request.session.get("order_total")
    cart_items = request.session.get("cart_items", [])
    address = request.session.get("delivery_address", "")
    date = request.session.get("delivery_date", "")
    payment_method = request.session.get("payment_method", "")
    special_instructions = request.session.get("special_instructions", "")
    subtotal = request.session.get("subtotal", total)
    commission = request.session.get("commission", "")
    producers = request.session.get("producers", [])
    bulk_discount = request.session.get("bulk_discount", "0.00")
    discounted_subtotal = request.session.get("discounted_subtotal", subtotal)
    is_bulk_buyer = bool(request.session.get("is_bulk_buyer", False))

    if request.method == "POST":
        address = request.POST.get("delivery_address", address)
        date = request.POST.get("delivery_date", date)
        payment_method = request.POST.get("payment_method", payment_method)
        postcode = request.session.get("delivery_postcode", "")

        request.session["delivery_address"] = address
        request.session["delivery_date"] = date
        request.session["payment_method"] = payment_method

        cart, _ = Cart.objects.get_or_create(user=request.user)
        db_cart_items = list(
            cart.items.select_related("product", "product__producer")
        )

        if not db_cart_items:
            messages.error(request, "Your cart is empty.")
            return redirect("cart:detail")

        try:
            if not date:
                messages.error(request, "Delivery date is missing.")
                return redirect("cart:checkout")

            try:
                delivery_date_obj = datetime.fromisoformat(date).date()
            except ValueError:
                messages.error(request, "Invalid delivery date.")
                return redirect("cart:checkout")

            with transaction.atomic():
                locked_products = {}

                for item in db_cart_items:
                    product = Product.objects.select_for_update().get(
                        pk=item.product_id
                    )

                    if not product.is_active:
                        raise ValueError(f"{product.name} is no longer available.")

                    if not product.is_in_season():
                        raise ValueError(f"{product.name} is currently out of season.")

                    is_bulk_buyer = (
                        hasattr(request.user, "profile")
                        and request.user.profile.role in {"COMMUNITY_GROUP", "RESTAURANT"}
                    )
                    if item.quantity > product.stock_quantity and not is_bulk_buyer:
                        raise ValueError(
                            f"Insufficient stock for {product.name}. "
                            f"Available: {product.stock_quantity}, requested: {item.quantity}."
                        )
                    locked_products[item.product_id] = product


                order = Order.objects.create(
                    customer=request.user,
                    delivery_address=address,
                    special_instructions=special_instructions,
                    delivery_postcode=postcode,
                    status=Order.Status.PENDING,
                    total_amount=Decimal("0.00"),
                )

                grouped_items = defaultdict(list)

                for item in db_cart_items:
                    grouped_items[item.product.producer].append(item)

                producer_order_summaries = []
                computed_subtotal = Decimal("0.00")
                overstock_items = []

                for producer, items in grouped_items.items():
                    producer_total = Decimal("0.00")
                    producer_order_items = []

                    producer_order = ProducerOrder.objects.create(
                        order=order,
                        producer=producer,
                        delivery_date=delivery_date_obj,
                        status=ProducerOrder.Status.PENDING,
                        total_value=Decimal("0.00"),
                    )

                    ProducerOrderStatusHistory.objects.create(
                        producer_order=producer_order,
                        old_status="",
                        new_status=ProducerOrder.Status.PENDING,
                        note="Order created",
                        changed_by=request.user,
                    )

                    for item in items:
                        product = locked_products[item.product_id]
                        priced_item, order_item_entries = _build_order_item_entries(
                            product,
                            item.quantity,
                        )

                        for entry in order_item_entries:
                            OrderItem.objects.create(
                                producer_order=producer_order,
                                product=product,
                                quantity=entry["quantity"],
                                unit_price=entry["unit_price"],
                            )

                        original_stock = product.stock_quantity
                        _update_product_inventory(
                            product,
                            item.quantity,
                            priced_item["discounted_qty"],
                        )
                        if is_bulk_buyer and item.quantity > original_stock:
                            overstock_items.append({
                                "product": product,
                                "ordered": item.quantity,
                                "available": original_stock,
                            })

                        producer_order_items.extend(order_item_entries)
                        producer_total += priced_item["total"]
                        computed_subtotal += priced_item["total"]

                    producer_order.total_value = producer_total.quantize(
                        Decimal("0.01")
                    )
                    producer_order.save(update_fields=["total_value"])

                    producer_order_summaries.append(
                        {
                            "producer": producer,
                            "producer_order": producer_order,
                            "items": producer_order_items,
                            "total": producer_order.total_value,
                        }
                    )

                bulk_discount = (
                    computed_subtotal * Decimal("0.10")
                ).quantize(Decimal("0.01")) if is_bulk_buyer else Decimal("0.00")

                discounted_subtotal = (
                    computed_subtotal - bulk_discount
                ).quantize(Decimal("0.01"))

                computed_commission = (
                    discounted_subtotal * Decimal("0.05")
                ).quantize(Decimal("0.01"))

                computed_total = (
                    discounted_subtotal + computed_commission
                ).quantize(Decimal("0.01"))

                order.total_amount = computed_total
                order.save(update_fields=["total_amount"])

                cart.items.all().delete()
                request.session["cart"] = {}

                # Save to CustomerOrderHistory for order history view
                order_number = f"BRFN-{order.id}"
                producers_data = {}
                for summary in producer_order_summaries:
                    producer_name = str(summary["producer"])
                    producers_data[producer_name] = [
                        {
                        "id": entry["product"].id,
                        "name": entry["product"].name,
                        "qty": entry["quantity"],
                        "price": str(entry["unit_price"]),
                        "total": str(entry["line_total"]),
                        }
                        for entry in summary["items"]
                    ]

                order_data = {
                    "order_number": order_number,
                    "address": address,
                    "order_date": timezone.now().strftime("%Y-%m-%d"),
                    "delivery_date": date,
                    "payment": payment_method,
                    "subtotal": str(computed_subtotal),
                    "bulk_discount": str(bulk_discount),
                    "discounted_subtotal": str(discounted_subtotal),
                    "commission": str(computed_commission),
                    "total": str(computed_total),
                    "producers": producers_data,
                    "special_instructions": special_instructions,
                }

                CustomerOrderHistory.objects.create(
                    customer=request.user,
                    order_number=order_number,
                    order_data=order_data,
                )
                
                for summary in producer_order_summaries:
                    po_total = summary["total"]
                    po_commission = (po_total * Decimal("0.05")).quantize(Decimal("0.01"))
                    po_payment = (po_total * Decimal("0.95")).quantize(Decimal("0.01"))
                    CommissionLog.objects.create(
                        order=order,
                        producer_order=summary["producer_order"],
                        order_total=computed_total,
                        commission_amount=po_commission,
                        producer_payment=po_payment,
                        producer=summary["producer"],
                        note=f"Auto-calculated at order placement. Bulk discount: £{bulk_discount}",
                    )
                # Notify producers of over-stock bulk orders
                from apps.message.models import MessageThread, Message
                for overstock in overstock_items:
                    producer = overstock["product"].producer
                    subject = f"Bulk Order #{order.id} — Stock Arrangement Required"
                    thread = MessageThread.objects.create(
                        subject=subject,
                        created_by=request.user,
                        related_order=order,
                    )
                    thread.participants.add(request.user, producer)
                    Message.objects.create(
                        thread=thread,
                        sender=request.user,
                        body=(
                            f"Dear {producer.username},\n\n"
                            f"A bulk order (#{order.id}) has been placed for "
                            f"{overstock['ordered']} {overstock['product'].unit} of "
                            f"{overstock['product'].name}, which exceeds your current "
                            f"stock of {overstock['available']} {overstock['product'].unit}.\n\n"
                            f"Please arrange the additional stock or contact the buyer "
                            f"to discuss lead times.\n\n"
                            f"Special instructions: {special_instructions or 'None'}\n\n"
                            f"Delivery date requested: {date}\n\n"
                            f"Thank you."
                        ),
                    )


                for key in [
                    "cart_items",
                    "order_total",
                    "delivery_address",
                    "delivery_postcode",
                    "delivery_date",
                    "payment_method",
                    "special_instructions",
                    "producers",
                    "subtotal",
                    "commission",
                ]:
                    request.session.pop(key, None)

                request.session.modified = True

        except ValueError as e:
            messages.error(request, str(e))
            return redirect("cart:checkout")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages) if hasattr(e, "messages") else str(e))
            return redirect("cart:checkout")

        context = {
            "order": order,
            "order_number": f"BRFN-{order.id}",
            "address": order.delivery_address,
            "date": date,
            "payment": payment_method,
            "special_instructions": order.special_instructions,
            "producer_order_summaries": producer_order_summaries,
            "subtotal": computed_subtotal,
            "bulk_discount": bulk_discount,
            "discounted_subtotal": discounted_subtotal,
            "commission": computed_commission,
            "total": computed_total,
            "is_bulk_buyer": is_bulk_buyer,
        }

        return render(request, "orders/confirmation.html", context)

    return render(
    request,
    "orders/payment.html",
    {
        "order": {
            "total": _as_money(total),
            "subtotal": _as_money(subtotal),
            "bulk_discount": _as_money(bulk_discount),
            "discounted_subtotal": _as_money(discounted_subtotal),
            "is_bulk_buyer": is_bulk_buyer,
            "commission": _as_money(commission),
            "address": address,
            "date": date,
            "payment": payment_method,
        },
        "cart_items": cart_items,
        "special_instructions": special_instructions,
        "producers": producers,
    },
    )


@login_required
def order_history(request):
    from apps.marketplace.models import RecurringOrder

    orders = list(
        Order.objects
        .filter(customer=request.user)
        .prefetch_related(
            "producer_orders",
            "producer_orders__items",
            "producer_orders__items__product",
            "producer_orders__status_history",
        )
        .order_by("-created_at")
    )

    for order in orders:
        order.total_value = _order_total(order)
        order.status_steps, order.status_current = _order_status_steps(order)
        order.item_count = sum(
            item.quantity
            for producer_order in order.producer_orders.all()
            for item in producer_order.items.all()
        )

    recurring_orders = RecurringOrder.objects.filter(
        customer=request.user
    ).prefetch_related("items", "items__product").order_by("-created_at")

    return render(
        request,
        "orders/history.html",
        {
            "orders": orders,
            "recurring_orders": recurring_orders,
        },
    )


@login_required
def order_detail(request, pk):
    order = (
        Order.objects
        .filter(customer=request.user, pk=pk)
        .prefetch_related(
            "producer_orders",
            "producer_orders__items",
            "producer_orders__items__product",
            "producer_orders__status_history",
            "producer_orders__status_history__changed_by",
        )
        .first()
    )

    if not order:
        messages.error(request, "Order not found.")
        return redirect("orders:history")

    order.total_value = _order_total(order)
    order.status_steps, order.status_current = _order_status_steps(order)

    producer_orders = list(order.producer_orders.all())

    for producer_order in producer_orders:
        producer_order.latest_update = producer_order.status_history.first()

        for entry in producer_order.status_history.all():
            entry.display_new_status = ProducerOrder.Status(entry.new_status).label
            entry.display_old_status = (
                ProducerOrder.Status(entry.old_status).label
                if entry.old_status in ProducerOrder.Status.values
                else "Created"
            )

    return render(
        request,
        "orders/detail.html",
        {
            "order": order,
            "producer_orders": producer_orders,
        },
    )