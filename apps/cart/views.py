from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.marketplace.models import Product
from apps.marketplace.forms import CheckoutForm
from apps.marketplace.services import expire_surplus_deals
from .models import Cart, CartItem


BUYER_ROLES = {"CUSTOMER", "COMMUNITY_GROUP", "RESTAURANT"}


def _can_use_cart(user):
    return (
        hasattr(user, "profile")
        and user.profile.role in BUYER_ROLES
    )


def _sync_session_cart(request, cart):
    """
    Keep the session cart in sync with the DB cart so existing checkout
    code that reads request.session['cart'] continues to work.
    """
    session_cart = {}

    for item in cart.items.all():
        session_cart[str(item.product_id)] = item.quantity

    request.session["cart"] = session_cart
    request.session.modified = True


def _build_cart_pricing(item):
    pricing = item.product.calculate_price_for_quantity(item.quantity)

    return {
        "product": item.product,
        "qty": item.quantity,
        "producer": item.product.producer,
        "line_total": pricing["total"],
        **pricing,
    }


@login_required
def cart_detail(request):
    expire_surplus_deals()

    cart, _ = Cart.objects.get_or_create(user=request.user)

    items = cart.items.select_related("product", "product__producer")
    cart_items = []
    total = Decimal("0.00")

    for item in items:
        priced_item = _build_cart_pricing(item)
        total += priced_item["line_total"]
        cart_items.append(priced_item)

    _sync_session_cart(request, cart)

    return render(request, "cart/detail.html", {
        "cart_items": cart_items,
        "cart_total": total.quantize(Decimal("0.01")),
    })


@require_POST
@login_required
def cart_add(request, product_id):
    expire_surplus_deals()

    if not _can_use_cart(request.user):
        messages.error(request, "Only buyer accounts can add items to the cart.")
        return redirect("marketplace:product_list")

    product = get_object_or_404(Product, id=product_id)

    # Block adding out-of-season products
    if not product.is_in_season():
        messages.error(request, f"'{product.name}' is currently out of season and cannot be ordered.")
        return redirect("marketplace:product_list")

    # Block adding unavailable products
    if not product.is_active or product.stock_quantity <= 0:
        messages.error(request, f"'{product.name}' is not currently available.")
        return redirect("marketplace:product_list")

    cart, _ = Cart.objects.get_or_create(user=request.user)

    try:
<<<<<<< HEAD
        qty = int(request.POST.get("qty") or request.POST.get("quantity") or 1)
=======
        qty = int(request.POST.get("qty"))
>>>>>>> dev
    except (TypeError, ValueError):
        qty = 1

    if qty < 1:
        qty = 1

    item, created = CartItem.objects.get_or_create(cart=cart, product=product)

    new_quantity = qty if created else item.quantity + qty

    if new_quantity > product.stock_quantity:
        messages.error(
            request,
            f"Cannot add more than available stock ({product.stock_quantity}) for {product.name}."
        )
        return redirect("cart:detail")

    item.quantity = new_quantity
    item.save()
    _sync_session_cart(request, cart)

    pricing = product.calculate_price_for_quantity(new_quantity)
    if pricing["warning"]:
        messages.warning(request, pricing["warning"])
    else:
        messages.success(request, f"Added {qty} {product.name} to your cart.")
    return redirect("cart:detail")

@require_POST
@login_required
def cart_update(request, product_id):
    expire_surplus_deals()

    if not _can_use_cart(request.user):
        messages.error(request, "Only buyer accounts can update the cart.")
        return redirect("marketplace:product_list")

    cart, _ = Cart.objects.get_or_create(user=request.user)
    item = get_object_or_404(CartItem, cart=cart, product_id=product_id)
    product = item.product

    try:
        qty = int(request.POST.get("qty", 1))
    except (TypeError, ValueError):
        qty = 1

    if qty <= 0:
        product_name = product.name
        item.delete()
        messages.info(request, f"Removed {product_name} from your cart.")
    else:
        if qty > product.stock_quantity:
            messages.error(request, f"Cannot add more than available stock ({product.stock_quantity}) for {product.name}.")
            qty = product.stock_quantity
        item.quantity = qty
        item.save()
        pricing = product.calculate_price_for_quantity(qty)
        if pricing["warning"]:
            messages.warning(request, pricing["warning"])
        else:
            messages.success(request, f"Updated {product.name} to {qty}.")

    _sync_session_cart(request, cart)
    return redirect("cart:detail")


@require_POST
@login_required
def cart_remove(request, product_id):
    if not _can_use_cart(request.user):
        messages.error(request, "Only buyer accounts can remove items from the cart.")
        return redirect("marketplace:product_list")

    cart, _ = Cart.objects.get_or_create(user=request.user)
    item = get_object_or_404(CartItem, cart=cart, product_id=product_id)

    product_name = item.product.name
    item.delete()

    _sync_session_cart(request, cart)

    messages.info(request, f"{product_name} removed from your cart.")
    return redirect("cart:detail")


@login_required
def checkout(request):
    expire_surplus_deals()

    cart, _ = Cart.objects.get_or_create(user=request.user)

    if not cart.items.exists():
        messages.info(request, "Your cart is empty.")
        return redirect("cart:detail")

    items = cart.items.select_related("product", "product__producer")
    producers = {}
    subtotal = Decimal("0.00")
    cart_items = []

    for item in items:
        priced_item = _build_cart_pricing(item)
        line_total = priced_item["line_total"]
        subtotal += line_total
        producer_name = item.product.producer.name if hasattr(item.product.producer, 'name') else str(item.product.producer)
        lead_time = getattr(item.product.producer, 'lead_time', 2)
        if producer_name not in producers:
            producers[producer_name] = []
        producers[producer_name].append({
            "name": item.product.name,
            "qty": item.quantity,
            "unit_price": str(priced_item["normal_unit_price"]),
            "discounted_unit_price": str(priced_item["discounted_unit_price"]),
            "discounted_qty": priced_item["discounted_qty"],
            "normal_qty": priced_item["normal_qty"],
            "total": str(line_total),
            "warning": priced_item["warning"],
            "lead_time": lead_time,
        })
        cart_items.append({
            "product": {"name": item.product.name, "id": item.product.id},
            "qty": item.quantity,
            "total": str(line_total),
        })

    commission = (subtotal * Decimal("0.05")).quantize(Decimal("0.01"))
    total = (subtotal + commission).quantize(Decimal("0.01"))

    _sync_session_cart(request, cart)

    initial = {}
    if hasattr(request.user, 'profile'):  
        if request.user.profile.delivery_address:
                initial['delivery_address'] = request.user.profile.delivery_address
        if request.user.profile.delivery_postcode:
            initial['delivery_postcode'] = request.user.profile.delivery_postcode
    form = CheckoutForm(initial=initial)

    if request.method == "POST":
        form = CheckoutForm(request.POST)
        if form.is_valid():
            request.session['order_total'] = str(total)
            request.session['cart_items'] = cart_items
            request.session['delivery_address'] = form.cleaned_data.get('delivery_address', '')
            request.session['delivery_postcode'] = form.cleaned_data.get('delivery_postcode', '')
            # Convert delivery_date to string for session storage
            delivery_date = form.cleaned_data.get('delivery_date', '')
            if hasattr(delivery_date, 'isoformat'):
                delivery_date = delivery_date.isoformat()
            request.session['delivery_date'] = delivery_date
            request.session['payment_method'] = form.cleaned_data.get('payment_method', '')
            request.session['producers'] = producers
            request.session['subtotal'] = str(subtotal)
            request.session['commission'] = str(commission)
            return redirect("orders:payment")

    return render(request, "cart/checkout.html", {
        "form": form,
        "producers": producers,
        "cart_items": cart_items,
        "subtotal": subtotal,
        "commission": commission,
        "total": total,
    })
