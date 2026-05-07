from decimal import Decimal
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_GET

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


def _calculate_total_food_miles(cart_items_qs, customer_postcode):
    """
    Given a queryset of cart items and a customer postcode,
    returns the total food miles (float) counting each producer only once.
    Returns None if postcode is missing or all lookups fail.
    """
    from apps.accounts.models import Profile
    from apps.marketplace.foodmiles import calculate_food_miles

    if not customer_postcode:
        return None

    total_food_miles = Decimal("0.00")
    seen_producers = set()

    for item in cart_items_qs:
        producer_id = item.product.producer.id
        if producer_id in seen_producers:
            continue
        try:
            producer_profile = Profile.objects.get(user=item.product.producer)
            producer_postcode = producer_profile.postcode
            if producer_postcode:
                miles = calculate_food_miles(customer_postcode, producer_postcode)
                if miles is not None:
                    total_food_miles += Decimal(str(miles))
                    seen_producers.add(producer_id)
        except Profile.DoesNotExist:
            pass

    return round(float(total_food_miles), 1)


@login_required
def cart_detail(request):
    expire_surplus_deals()

    cart, _ = Cart.objects.get_or_create(user=request.user)

    items = cart.items.select_related(
        "product",
        "product__producer",
        "product__category",
    )

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
        priced_item = _build_cart_pricing(item)
        total += priced_item["line_total"]

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

        priced_item["food_miles"] = food_miles
        priced_item["unit_price"] = priced_item.get("normal_unit_price", item.product.price)
        cart_items.append(priced_item)

    # Suggestions like Amazon:
    # 1. First suggest other products from the same producers
    # 2. If not enough, suggest products from similar categories
    cart_product_ids = [item["product"].id for item in cart_items]
    cart_producer_ids = [item["product"].producer.id for item in cart_items]
    cart_category_ids = [
        item["product"].category.id
        for item in cart_items
        if item["product"].category
    ]

    suggested_products = Product.objects.none()

    if cart_items:
        suggested_products = (
            Product.objects
            .filter(
                is_active=True,
                stock_quantity__gt=0,
                producer_id__in=cart_producer_ids,
            )
            .exclude(id__in=cart_product_ids)
            .select_related("producer", "category")
            .order_by("name")[:4]
        )

        if not suggested_products.exists() and cart_category_ids:
            suggested_products = (
                Product.objects
                .filter(
                    is_active=True,
                    stock_quantity__gt=0,
                    category_id__in=cart_category_ids,
                )
                .exclude(id__in=cart_product_ids)
                .select_related("producer", "category")
                .order_by("name")[:4]
            )

    # Suggestions like Amazon:
    # 1. First suggest other products from the same producers
    # 2. If not enough, suggest products from similar categories
    cart_product_ids = [item["product"].id for item in cart_items]
    cart_producer_ids = [item["product"].producer.id for item in cart_items]
    cart_category_ids = [
        item["product"].category.id
        for item in cart_items
        if item["product"].category
    ]

    suggested_products = Product.objects.none()

    if cart_items:
        suggested_products = (
            Product.objects
            .filter(
                is_active=True,
                stock_quantity__gt=0,
                producer_id__in=cart_producer_ids,
            )
            .exclude(id__in=cart_product_ids)
            .select_related("producer", "category")
            .order_by("name")[:4]
        )

        if not suggested_products.exists() and cart_category_ids:
            suggested_products = (
                Product.objects
                .filter(
                    is_active=True,
                    stock_quantity__gt=0,
                    category_id__in=cart_category_ids,
                )
                .exclude(id__in=cart_product_ids)
                .select_related("producer", "category")
                .order_by("name")[:4]
            )

    _sync_session_cart(request, cart)

    is_bulk_buyer = (
        hasattr(request.user, "profile")
        and request.user.profile.role in {"COMMUNITY_GROUP", "RESTAURANT"}
    )
    bulk_discount = (total * Decimal("0.10")).quantize(Decimal("0.01")) if is_bulk_buyer else Decimal("0.00")
    discounted_total = (total - bulk_discount).quantize(Decimal("0.01"))

    return render(request, "cart/detail.html", {
        "cart_items": cart_items,
        "cart_total": total.quantize(Decimal("0.01")),
        "bulk_discount": bulk_discount,
        "discounted_total": discounted_total,
        "is_bulk_buyer": is_bulk_buyer,
        "total_food_miles": round(float(total_food_miles), 1),
        "suggested_products": suggested_products,
    })


@require_POST
@login_required
def cart_add(request, product_id):
    expire_surplus_deals()

    if not _can_use_cart(request.user):
        messages.error(request, "Only buyer accounts can add items to the cart.")
        return redirect("marketplace:product_list")

    product = get_object_or_404(Product, id=product_id)

    if not product.is_in_season():
        messages.error(request, f"'{product.name}' is currently out of season and cannot be ordered.")
        return redirect("marketplace:product_list")

    if not product.is_active or product.stock_quantity <= 0:
        messages.error(request, f"'{product.name}' is not currently available.")
        return redirect("marketplace:product_list")

    cart, _ = Cart.objects.get_or_create(user=request.user)

    try:
        qty = int(request.POST.get("qty", 1) or request.POST.get("quantity") or 1)
    except (TypeError, ValueError):
        qty = 1

    if qty < 1:
        qty = 1

    # ── Bulk buyer rules ───────────────────────────────────────────
    is_bulk_buyer = (
        hasattr(request.user, "profile")
        and request.user.profile.role in {"COMMUNITY_GROUP", "RESTAURANT"}
    )

    if is_bulk_buyer:
        existing = CartItem.objects.filter(cart=cart, product=product).first()
        new_quantity = qty if not existing else existing.quantity + qty

        if new_quantity < 5:
            messages.error(
                request,
                f"Bulk orders require a minimum of 5 units per item. "
                f"Please order at least 5 {product.unit} of {product.name}."
            )
            return redirect("marketplace:product_list")

        item, created = CartItem.objects.get_or_create(cart=cart, product=product)

        # Allow ordering above stock — producer will be notified
        if new_quantity > product.stock_quantity:
            messages.warning(
                request,
                f"You have requested {new_quantity} {product.unit} of {product.name}, "
                f"but only {product.stock_quantity} are currently in stock. "
                f"Your order will be placed and the producer will arrange the remainder."
            )

        item.quantity = new_quantity
        item.save()
        _sync_session_cart(request, cart)
        messages.success(request, f"Added {qty} {product.name} to your cart (bulk order).")
        return redirect("cart:detail")

    # ── Regular customer rules ─────────────────────────────────────
    existing = CartItem.objects.filter(cart=cart, product=product).first()
    new_quantity = qty if not existing else existing.quantity + qty

    if new_quantity > product.stock_quantity:
        messages.error(
            request,
            f"Cannot add more than available stock ({product.stock_quantity}) for {product.name}."
        )
        return redirect("cart:detail")

    item, created = CartItem.objects.get_or_create(cart=cart, product=product)
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
        is_bulk_buyer = (
            hasattr(request.user, "profile")
            and request.user.profile.role in {"COMMUNITY_GROUP", "RESTAURANT"}
        )
        if qty > product.stock_quantity and not is_bulk_buyer:
            messages.error(
                request,
                f"Cannot add more than available stock ({product.stock_quantity}) for {product.name}."
            )
            qty = product.stock_quantity
        elif qty > product.stock_quantity and is_bulk_buyer:
            messages.warning(
                request,
                f"You have requested {qty} {product.unit} of {product.name}, "
                f"but only {product.stock_quantity} are currently in stock. "
                f"Your order will be placed and the producer will arrange the remainder."
            )

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

        producer_name = item.product.producer.username
        lead_time = getattr(item.product.producer, "lead_time", 2)

        if producer_name not in producers:
            producers[producer_name] = []

        producers[producer_name].append({
            "name": item.product.name,
            "id": item.product.id,
            "price": float(item.product.price),
            "qty": item.quantity,
            "unit_price": str(priced_item.get("normal_unit_price", item.product.price)),
            "discounted_unit_price": str(priced_item.get("discounted_unit_price", item.product.price)),
            "discounted_qty": priced_item.get("discounted_qty", 0),
            "normal_qty": priced_item.get("normal_qty", item.quantity),
            "total": float(line_total),
            "warning": priced_item.get("warning", ""),
            "lead_time": lead_time,
        })

        cart_items.append({
            "product": {
                "name": item.product.name,
                "id": item.product.id,
            },
            "qty": item.quantity,
            "total": str(line_total),
        })

#Bulk buyer discount (10%)

    is_bulk_buyer = (
        hasattr(request.user, "profile")
        and request.user.profile.role in {"COMMUNITY_GROUP", "RESTAURANT"}
    )
    bulk_discount = (subtotal * Decimal("0.10")).quantize(Decimal("0.01")) if is_bulk_buyer else Decimal("0.00")
    discounted_subtotal = (subtotal - bulk_discount).quantize(Decimal("0.01"))
    commission = (discounted_subtotal * Decimal("0.05")).quantize(Decimal("0.01"))
    total = (discounted_subtotal + commission).quantize(Decimal("0.01"))
    _sync_session_cart(request, cart)

    # Determine initial postcode for food miles display
    initial_postcode = None
    initial = {}

    if hasattr(request.user, "profile"):
        if getattr(request.user.profile, "delivery_address", None):
            initial["delivery_address"] = request.user.profile.delivery_address

        if getattr(request.user.profile, "delivery_postcode", None):
            initial["delivery_postcode"] = request.user.profile.delivery_postcode

    form = CheckoutForm(initial=initial)

    # Calculate initial food miles using profile postcode
    total_food_miles = _calculate_total_food_miles(items, initial_postcode)

    if request.method == "POST":
        form = CheckoutForm(request.POST)

        if form.is_valid():
            delivery_date = form.cleaned_data.get("delivery_date", "")

            if hasattr(delivery_date, "isoformat"):
                delivery_date = delivery_date.isoformat()

            request.session["order"] = {
                "address": form.cleaned_data.get("delivery_address", ""),
                "postcode": form.cleaned_data.get("delivery_postcode", ""),
                "date": delivery_date,
                "payment": form.cleaned_data.get("payment_method", ""),
                "subtotal": float(subtotal),
                "commission": float(commission),
                "total": float(total),
                "producers": producers,
            }

            request.session["order_total"] = str(total)
            request.session["cart_items"] = cart_items
            request.session["delivery_address"] = form.cleaned_data.get("delivery_address", "")
            request.session["delivery_postcode"] = form.cleaned_data.get("delivery_postcode", "")
            request.session["delivery_date"] = delivery_date
            request.session["payment_method"] = form.cleaned_data.get("payment_method", "")
            request.session["producers"] = producers
            request.session["subtotal"] = str(subtotal)
            request.session["commission"] = str(commission)
            request.session.modified = True

            return redirect("marketplace:payment")

    return render(request, "cart/checkout.html", {
        "form": form,
        "producers": producers,
        "cart_items": cart_items,
        "subtotal": subtotal,
        "bulk_discount": bulk_discount,
        "is_bulk_buyer": is_bulk_buyer,
        "discounted_subtotal": discounted_subtotal,
        "commission": commission,
        "total": total,
        "total_food_miles": total_food_miles,
    })


@require_GET
@login_required
def food_miles_ajax(request):
    """
    AJAX endpoint — returns food miles for a given postcode.
    Called by the checkout page when the user changes their delivery postcode.
    GET param: postcode
    Returns: { "food_miles": 12.3 } or { "food_miles": null }
    """
    postcode = request.GET.get("postcode", "").strip()

    if not postcode:
        return JsonResponse({"food_miles": None})

    cart, _ = Cart.objects.get_or_create(user=request.user)
    items = cart.items.select_related("product", "product__producer")

    total_food_miles = _calculate_total_food_miles(items, postcode)

    return JsonResponse({"food_miles": total_food_miles})