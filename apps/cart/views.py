<<<<<<< HEAD

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.contrib import messages
=======
from decimal import Decimal

from django.contrib.auth.decorators import login_required
>>>>>>> melee
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from apps.marketplace.models import Product
<<<<<<< HEAD
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
=======


def cart_detail(request):
    cart = request.session.get("cart", {})  

    product_ids = list(cart.keys())
    products = Product.objects.select_related("producer").filter(id__in=product_ids)

    found_ids = {str(p.id) for p in products}
    missing_ids = [pid for pid in cart.keys() if pid not in found_ids]
    if missing_ids:
        for pid in missing_ids:
            cart.pop(pid, None)
        request.session["cart"] = cart
        request.session.modified = True

    items = []
    total = Decimal("0.00")

    for product in products:
        qty = int(cart.get(str(product.id), 0))
        line_total = (product.price * qty).quantize(Decimal("0.01"))
        total += line_total

        items.append({
            "product": product,
            "qty": qty,
            "unit_price": product.price,
            "line_total": line_total,
            "producer": product.producer,
        })

    return render(request, "cart/detail.html", {
        "cart_items": items,
>>>>>>> melee
        "cart_total": total.quantize(Decimal("0.01")),
    })


@require_POST
<<<<<<< HEAD
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
=======
def cart_add(request, product_id):
    product = Product.objects.get(id=product_id)

    cart = request.session.get("cart", {})
    cart[str(product_id)] = cart.get(str(product_id), 0) + 1

    request.session["cart"] = cart

>>>>>>> melee
    return redirect("cart:detail")


@require_POST
<<<<<<< HEAD
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

=======
def cart_update(request, product_id):
    cart = request.session.get("cart", {})
    qty = int(request.POST.get("qty", 1))

    pid = str(product_id)

    if qty <= 0:
        cart.pop(pid, None)
    else:
        cart[pid] = qty

    request.session["cart"] = cart
    request.session.modified = True
>>>>>>> melee
    return redirect("cart:detail")


@require_POST
<<<<<<< HEAD
@login_required
def cart_remove(request, product_id):
    if request.user.profile.role != "CUSTOMER":
        messages.error(request, "Only customers can remove items from the cart.")
        return redirect("marketplace:product_list")

    cart, _ = Cart.objects.get_or_create(user=request.user)
    item = get_object_or_404(CartItem, cart=cart, product_id=product_id)

    item.delete()
    messages.info(request, "Item removed from your cart.")
=======
def cart_remove(request, product_id):
    cart = request.session.get("cart", {})
    cart.pop(str(product_id), None)
    request.session["cart"] = cart
    request.session.modified = True
>>>>>>> melee
    return redirect("cart:detail")


@login_required
def checkout(request):
<<<<<<< HEAD
    cart, _ = Cart.objects.get_or_create(user=request.user)

    if not cart.items.exists():
        return redirect("cart:detail")

=======
    cart = request.session.get("cart", {})
    if not cart:
        return redirect("cart:detail")
>>>>>>> melee
    return render(request, "cart/checkout.html")