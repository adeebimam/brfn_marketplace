from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render, redirect
from django.utils import timezone
from datetime import datetime
from apps.cart.models import Cart
from apps.marketplace.models import Order, ProducerOrder, OrderItem, Product, ProducerOrderStatusHistory


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
    total = request.session.get("order_total")
    cart_items = request.session.get("cart_items", [])
    address = request.session.get("delivery_address", "")
    date = request.session.get("delivery_date", "")
    payment_method = request.session.get("payment_method", "")
    producers = request.session.get("producers", {})
    subtotal = request.session.get("subtotal", total)
    commission = request.session.get("commission", "")

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
                # Lock product rows and validate stock again
                locked_products = {}
                for item in db_cart_items:
                    product = Product.objects.select_for_update().get(pk=item.product_id)

                    if not product.is_active:
                        raise ValueError(f"{product.name} is no longer available.")

                    if not product.is_in_season():
                        raise ValueError(f"{product.name} is currently out of season.")

                    if item.quantity > product.stock_quantity:
                        raise ValueError(
                            f"Insufficient stock for {product.name}. "
                            f"Available: {product.stock_quantity}, requested: {item.quantity}."
                        )

                    locked_products[item.product_id] = product

                # Create main customer order
                order = Order.objects.create(
                    customer=request.user,
                    delivery_address=address,
                    special_instructions="",
                    delivery_postcode=postcode,
                    status=Order.Status.PENDING,
                )

                # Group cart items by producer
                grouped_items = defaultdict(list)
                for item in db_cart_items:
                    grouped_items[item.product.producer].append(item)

                producer_order_summaries = []
                computed_subtotal = Decimal("0.00")

                # Create producer orders + order items + update stock
                for producer, items in grouped_items.items():
                    producer_total = Decimal("0.00")

                    producer_order = ProducerOrder.objects.create(
                        order=order,
                        producer=producer,
                        delivery_date=delivery_date_obj,
                        status=ProducerOrder.Status.PENDING,
                        total_value=Decimal("0.00"),
                    )
                    ProducerOrderStatusHistory.objects.create(
                        producer_order=producer_order,
                        old_status = "",
                        new_status=ProducerOrder.Status.PENDING,
                        note="Order created",
                        changed_by=request.user,
                    )

                    for item in items:
                        product = locked_products[item.product_id]
                        priced_item = _build_priced_cart_item(item)

                        if priced_item["discounted_qty"] > 0:
                            OrderItem.objects.create(
                                producer_order=producer_order,
                                product=product,
                                quantity=priced_item["discounted_qty"],
                                unit_price=priced_item["discounted_unit_price"],
                            )

                        if priced_item["normal_qty"] > 0:
                            OrderItem.objects.create(
                                producer_order=producer_order,
                                product=product,
                                quantity=priced_item["normal_qty"],
                                unit_price=priced_item["normal_unit_price"],
                            )

                        product.stock_quantity -= item.quantity
                        if priced_item["discounted_qty"] > 0:
                            product.surplus_stock_quantity = max(
                                0,
                                product.surplus_stock_quantity - priced_item["discounted_qty"],
                            )
                        product.surplus_stock_quantity = min(
                            product.surplus_stock_quantity,
                            product.stock_quantity,
                        )
                        product.save(update_fields=["stock_quantity", "surplus_stock_quantity"])

                        producer_total += priced_item["line_total"]
                        computed_subtotal += priced_item["line_total"]

                    producer_order.total_value = producer_total.quantize(Decimal("0.01"))
                    producer_order.save(update_fields=["total_value"])

                    producer_order_summaries.append({
                        "producer": producer,
                        "producer_order": producer_order,
                        "items": [_build_priced_cart_item(item) for item in items],
                        "total": producer_order.total_value,
                    })

                computed_commission = (computed_subtotal * Decimal("0.05")).quantize(Decimal("0.01"))
                computed_total = (computed_subtotal + computed_commission).quantize(Decimal("0.01"))

                # Clear cart
                cart.items.all().delete()

                # Clear session checkout/cart data
                request.session["cart"] = {}
                for key in [
                    "cart_items",
                    "order_total",
                    "delivery_address",
                    "delivery_postcode",
                    "delivery_date",
                    "payment_method",
                    "producers",
                    "subtotal",
                    "commission",
                ]:
                    request.session.pop(key, None)

                request.session.modified = True

        except ValueError as e:
            messages.error(request, str(e))
            return redirect("cart:checkout")

        context = {
            "order": order,
            "order_number": f"BRFN-{order.id}",
            "address": order.delivery_address,
            "date": date,
            "payment": payment_method,
            "producer_order_summaries": producer_order_summaries,
            "subtotal": computed_subtotal,
            "commission": computed_commission,
            "total": computed_total,
        }
        return render(request, "orders/confirmation.html", context)

    return render(request, "orders/payment.html", {
        "total": _as_money(total),
        "subtotal": _as_money(subtotal),
        "commission": _as_money(commission),
        "cart_items": cart_items,
        "address": address,
        "date": date,
        "payment_method": payment_method,
        "producers": producers,
    })


@login_required
def order_history(request):
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

    return render(request, "orders/history.html", {
        "orders": orders,
    })


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

    return render(request, "orders/detail.html", {
        "order": order,
        "producer_orders": producer_orders,
    })
