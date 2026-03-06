
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from apps.marketplace.models import Product
from .models import Cart, CartItem


@login_required
def cart_detail(request):
    # cart for the loggedin user
    cart, _ = Cart.objects.get_or_create(user=request.user)

    # Load cart items with product and producer data
    items = cart.items.select_related("product", "product__producer")

    cart_items = []
    total = Decimal("0.00")

    for item in items:
        line_total = (item.product.price * item.quantity).quantize(Decimal("0.01"))
        total += line_total

        cart_items.append({
            "product": item.product,
            "qty": item.quantity,
            "unit_price": item.product.price,
            "line_total": line_total,
            "producer": item.product.producer,
        })

    return render(request, "cart/detail.html", {
        "cart_items": cart_items,
        "cart_total": total.quantize(Decimal("0.01")),
    })


@require_POST
@login_required
def cart_add(request, product_id):
    # Only customers can add items to the cart
    if request.user.profile.role != "CUSTOMER":
        messages.error(request, "Only customers can add items to the cart.")
        return redirect("marketplace:product_list")

    product = get_object_or_404(Product, id=product_id)
    cart, _ = Cart.objects.get_or_create(user=request.user)

    try:
        qty = int(request.POST.get("qty", 1))
    except (TypeError, ValueError):
        qty = 1

    if qty < 1:
        qty = 1

    item, created = CartItem.objects.get_or_create(cart=cart, product=product)

    if created:
        item.quantity = qty
    else:
        item.quantity += qty

    item.save()

    messages.success(request, f"Added {qty} {product.name} to your cart.")
    return redirect("cart:detail")


@require_POST
@login_required
def cart_update(request, product_id):
    if request.user.profile.role != "CUSTOMER":
        messages.error(request, "Only customers can update the cart.")
        return redirect("marketplace:product_list")

    cart, _ = Cart.objects.get_or_create(user=request.user)
    item = get_object_or_404(CartItem, cart=cart, product_id=product_id)

    try:
        qty = int(request.POST.get("qty", 1))
    except (TypeError, ValueError):
        qty = 1

    if qty <= 0:
        item.delete()
        messages.info(request, f"Removed {item.product.name} from your cart.")
    else:
        item.quantity = qty
        item.save()
        messages.success(request, f"Updated {item.product.name} to {qty}.")

    return redirect("cart:detail")


@require_POST
@login_required
def cart_remove(request, product_id):
    if request.user.profile.role != "CUSTOMER":
        messages.error(request, "Only customers can remove items from the cart.")
        return redirect("marketplace:product_list")

    cart, _ = Cart.objects.get_or_create(user=request.user)
    item = get_object_or_404(CartItem, cart=cart, product_id=product_id)

    item.delete()
    messages.info(request, "Item removed from your cart.")
    return redirect("cart:detail")


@login_required
def checkout(request):
    cart, _ = Cart.objects.get_or_create(user=request.user)

    if not cart.items.exists():
        return redirect("cart:detail")

    return render(request, "cart/checkout.html")