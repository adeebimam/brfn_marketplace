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
                        line_total = (product.price * item.quantity).quantize(Decimal("0.01"))

                        OrderItem.objects.create(
                            producer_order=producer_order,
                            product=product,
                            quantity=item.quantity,
                            unit_price=product.price,
                        )

                        product.stock_quantity -= item.quantity
                        product.save(update_fields=["stock_quantity"])

                        producer_total += line_total
                        computed_subtotal += line_total

                    producer_order.total_value = producer_total.quantize(Decimal("0.01"))
                    producer_order.save(update_fields=["total_value"])

                    producer_order_summaries.append({
                        "producer": producer,
                        "producer_order": producer_order,
                        "items": items,
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
        "total": total,
        "cart_items": cart_items,
    })