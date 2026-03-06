from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.urls import reverse

from apps.marketplace.models import Product


# Display cart. Hides from anonymous users.
def cart_detail(request):
    
    if not request.user.is_authenticated:
        return render(request, "cart/detail.html", {
            "cart_items": [],
            "cart_total": Decimal("0.00"),
        })
        

    cart = request.session.get("cart", {})  

    product_ids = list(cart.keys())
    products = Product.objects.select_related("producer").filter(id__in=product_ids)

    # clean up cart: remove products that no longer exist
    found_ids = {str(p.id) for p in products}
    missing_ids = [pid for pid in cart.keys() if pid not in found_ids]
    if missing_ids:
        for pid in missing_ids:
            cart.pop(pid, None)
        request.session["cart"] = cart
        request.session.modified = True

    items = []
    total = Decimal("0.00")

    # build items list with line totals
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
        "cart_total": total.quantize(Decimal("0.01")),
    })

@require_POST
@login_required
def cart_add(request, product_id):

    # only customers can add to cart
    if request.user.profile.role!="CUSTOMER":
        messages.error(request, "Only customers can add items to the cart.")
        return redirect("marketplace:product_list")
    
    """Add product to cart. Qty <= 0 removes it."""
    product = get_object_or_404(Product, id=product_id)
    cart = request.session.get("cart", {})
    pid = str(product_id)

    # safely parse qty
    try:
        qty = int(request.POST.get("qty", 1))
    except (TypeError, ValueError):
        qty = 1

    if qty <= 0:
        cart.pop(pid, None)
        messages.info(request, f"Removed {product.name} from your cart.")
    else:
        cart[pid] = qty
        messages.success(request, f"Added {qty} {product.name}  to your cart.")

    request.session["cart"] = cart
    request.session.modified = True
    return redirect("cart:detail")


@require_POST
@login_required
def cart_update(request, product_id):
    """Update cart item qty. Qty <= 0 removes it."""
    product = get_object_or_404(Product, id=product_id)
    cart = request.session.get("cart", {})
    try:
        qty = int(request.POST.get("qty", 1))
    except (TypeError, ValueError):
        qty = 1

    pid = str(product_id)

    if qty <= 0:
        cart.pop(pid, None)
        messages.info(request, f"Removed {product.name} from your cart.")
    else:
        cart[pid] = qty
        messages.success(request, f"Updated {product.name} to {qty}.")

    request.session["cart"] = cart
    request.session.modified = True
    return redirect("cart:detail")


@require_POST
@login_required
def cart_remove(request, product_id):
    """Remove product from cart."""
    cart = request.session.get("cart", {})
    cart.pop(str(product_id), None)
    request.session["cart"] = cart
    request.session.modified = True
    messages.info(request, "Item removed from your cart.")
    return redirect("cart:detail")


@login_required
def checkout(request):
    """Show checkout page. Redirect if cart empty."""
    cart = request.session.get("cart", {})
    if not cart:
        return redirect("cart:detail")
    return render(request, "cart/checkout.html")