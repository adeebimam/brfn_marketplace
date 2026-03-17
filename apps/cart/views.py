from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from apps.marketplace.models import Product


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
        "cart_total": total.quantize(Decimal("0.01")),
    })


@require_POST
def cart_add(request, product_id):
    product = Product.objects.get(id=product_id)

    cart = request.session.get("cart", {})
    cart[str(product_id)] = cart.get(str(product_id), 0) + 1

    request.session["cart"] = cart

    return redirect("cart:detail")


@require_POST
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
    return redirect("cart:detail")


@require_POST
def cart_remove(request, product_id):
    cart = request.session.get("cart", {})
    cart.pop(str(product_id), None)
    request.session["cart"] = cart
    request.session.modified = True
    return redirect("cart:detail")


@login_required
def checkout(request):
    cart = request.session.get("cart", {})
    if not cart:
        return redirect("cart:detail")
    return render(request, "cart/checkout.html")