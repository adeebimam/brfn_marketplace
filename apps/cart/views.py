from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.marketplace.models import Product
from apps.marketplace.forms import CheckoutForm
from .models import Cart, CartItem

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


@login_required
def cart_detail(request):
    cart, _ = Cart.objects.get_or_create(user=request.user)

    items = cart.items.select_related("product", "product__producer")
    cart_items = []
    total = Decimal("0.00")
    total_food_miles = Decimal("0.00")
    seen_producers = set()

    from apps.accounts.models import Profile
    from apps.marketplace.foodmiles import calculate_food_miles

    customer_postcode = None
    try:
        customer_profile = Profile.objects.get(user=request.user)
        customer_postcode = customer_profile.delivery_postcode or customer_profile.postcode
    except Profile.DoesNotExist:
        pass

    for item in items:
        line_total = (item.product.price * item.quantity).quantize(Decimal("0.01"))
        total += line_total

        food_miles = None
        if customer_postcode:
            try:
                producer_profile = Profile.objects.get(user=item.product.producer)
                producer_postcode = producer_profile.postcode
                if producer_postcode:
                    food_miles = calculate_food_miles(customer_postcode, producer_postcode)
                    producer_id = item.product.producer.id
                    if food_miles is not None and producer_id not in seen_producers:
                        total_food_miles += Decimal(str(food_miles))
                        seen_producers.add(producer_id)
            except Profile.DoesNotExist:
                pass

        cart_items.append({
            "product": item.product,
            "qty": item.quantity,
            "unit_price": item.product.price,
            "line_total": line_total,
            "producer": item.product.producer,
            "food_miles": food_miles,
        })

    _sync_session_cart(request, cart)

    return render(request, "cart/detail.html", {
        "cart_items": cart_items,
        "cart_total": total.quantize(Decimal("0.01")),
        "total_food_miles": round(float(total_food_miles), 1),
    })


@require_POST
@login_required
def cart_add(request, product_id):
    if request.user.profile.role != "CUSTOMER":
        messages.error(request, "Only customers can add items to the cart.")
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
    _sync_session_cart(request, cart)

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
        product_name = item.product.name
        item.delete()
        messages.info(request, f"Removed {product_name} from your cart.")
    else:
        item.quantity = qty
        item.save()
        messages.success(request, f"Updated {item.product.name} to {qty}.")

    _sync_session_cart(request, cart)
    return redirect("cart:detail")


@require_POST
@login_required
def cart_remove(request, product_id):
    if request.user.profile.role != "CUSTOMER":
        messages.error(request, "Only customers can remove items from the cart.")
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
    cart, _ = Cart.objects.get_or_create(user=request.user)

    if not cart.items.exists():
        messages.info(request, "Your cart is empty.")
        return redirect("cart:detail")

    items = cart.items.select_related("product", "product__producer")
    producers = {}
    subtotal = Decimal("0.00")
    cart_items = []

    for item in items:
        line_total = (item.product.price * item.quantity).quantize(Decimal("0.01"))
        subtotal += line_total
        producer_name = item.product.producer.name if hasattr(item.product.producer, 'name') else str(item.product.producer)
        lead_time = getattr(item.product.producer, 'lead_time', 2)
        if producer_name not in producers:
            producers[producer_name] = []
        producers[producer_name].append({
            "name": item.product.name,
            "qty": item.quantity,
            "unit_price": str(item.product.price),
            "total": str(line_total),
            "lead_time": lead_time,
        })
        cart_items.append({
            "product": {"name": item.product.name, "id": item.product.id},
            "qty": item.quantity
        })

    commission = (subtotal * Decimal("0.05")).quantize(Decimal("0.01"))
    total = (subtotal + commission).quantize(Decimal("0.01"))

    _sync_session_cart(request, cart)

    initial = {}
    if hasattr(request.user, 'profile') and request.user.profile.delivery_address:
        initial['delivery_address'] = request.user.profile.delivery_address
    form = CheckoutForm(initial=initial)

    if request.method == "POST":
        form = CheckoutForm(request.POST)
        if form.is_valid():
            request.session['order_total'] = str(total)
            request.session['cart_items'] = cart_items
            request.session['delivery_address'] = form.cleaned_data.get('delivery_address', '')
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
        "subtotal": subtotal,
        "commission": commission,
        "total": total,
    })