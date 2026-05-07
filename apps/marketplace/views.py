import csv
import json
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncWeek
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import Profile
from apps.cart.models import Cart, CartItem

from .forms import CheckoutForm, ProductForm, ProducerOrderStatusForm, PurchaseReviewForm, ReviewForm
from .models import (
    Allergen,
    Category,
    CustomerOrderHistory,
    FavouriteProducer,
    MONTH_NAMES,
    Order,
    OrderItem,
    Product,
    ProducerOrder,
    ProducerOrderStatusHistory,
    PurchaseReview,
    Review,
    StockNotification,
    SurplusAnalyticsRecord,
    SurplusDealNotification,
)
from .services import (
    expire_surplus_deals,
    notify_favourite_customers_about_surplus,
    update_producer_order_status,
)


COMMISSION_RATE = Decimal("0.05")
TWO_PLACES = Decimal("0.01")
BUYER_ROLES = {
    Profile.Role.CUSTOMER,
    Profile.Role.COMMUNITY_GROUP,
    Profile.Role.RESTAURANT,
}


# -----------------------------
# Helper functions
# -----------------------------

def _compute_financials(gross):
    gross = Decimal(gross).quantize(TWO_PLACES)
    commission = (gross * COMMISSION_RATE).quantize(TWO_PLACES)
    net = (gross - commission).quantize(TWO_PLACES)
    return commission, net


def _build_settlement_ref(producer_id, week_start, week_end):
    return (
        f"SET-{producer_id}-"
        f"{week_start.strftime('%Y%m%d')}-"
        f"{week_end.strftime('%Y%m%d')}"
    )


def _last_completed_week_range(today):
    current_week_start = today - timedelta(days=today.weekday())
    week_end = current_week_start - timedelta(days=1)
    week_start = week_end - timedelta(days=6)
    return week_start, week_end


def _producer_settlement_week_range(producer, today):
    week_start, week_end = _last_completed_week_range(today)
    delivered_orders = ProducerOrder.objects.filter(
        producer=producer,
        status=ProducerOrder.Status.DELIVERED,
        delivery_date__isnull=False,
    )

    if not delivered_orders.exists():
        return week_start, week_end

    if delivered_orders.filter(delivery_date__range=(week_start, week_end)).exists():
        return week_start, week_end

    latest_delivery = delivered_orders.order_by("-delivery_date").values_list(
        "delivery_date", flat=True
    ).first()
    if latest_delivery is None:
        return week_start, week_end

    fallback_start = latest_delivery - timedelta(days=latest_delivery.weekday())
    fallback_end = fallback_start + timedelta(days=6)
    return fallback_start, fallback_end


def _uk_tax_year_start(today):
    tax_year_start = date(today.year, 4, 6)
    if today < tax_year_start:
        tax_year_start = date(today.year - 1, 4, 6)
    return tax_year_start


def _payment_template_context(order, **extra):
    payment_value = str(order.get("payment", "")).strip().lower()
    is_paypal_sandbox = payment_value in {"paypal", "paypal_sandbox"}
    context = {
        "order": order,
        "is_paypal_sandbox": is_paypal_sandbox,
        "paypal_client_id": settings.PAYPAL_SANDBOX_CLIENT_ID,
    }
    context.update(extra)
    return context


def _anonymise_customer(customer):
    if not customer:
        return "Unknown customer"
    full_name = customer.get_full_name().strip()
    if full_name:
        parts = full_name.split()
        if len(parts) == 1:
            return parts[0]
        return f"{parts[0]} {parts[-1][0]}."
    return customer.username


def _get_customer_order_history(user):
    return [
        record.order_data
        for record in CustomerOrderHistory.objects.filter(customer=user).order_by("-id")
    ]


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _has_purchased_product(order_history, product_id):
    for order in order_history:
        for producer, items in order.get("producers", {}).items():
            for item in items:
                if str(item.get("id")) == str(product_id):
                    return True
    return False


def _get_product_unit_price(product):
    if product.is_active_surplus_deal and product.discounted_price is not None:
        return product.discounted_price
    return product.price


def _attach_producer_order_context(producer_orders):
    for producer_order in producer_orders:
        order_date = timezone.localtime(producer_order.order.created_at).date()
        if producer_order.delivery_date:
            producer_order.lead_time_days = max(
                (producer_order.delivery_date - order_date).days, 0,
            )
        else:
            producer_order.lead_time_days = 0
        producer_order.item_summary = ", ".join(
            f"{item.product.name} x{item.quantity}"
            for item in producer_order.items.all()
        )
    return producer_orders


# -----------------------------
# Producer access check
# -----------------------------

def _require_producer(request, verified_only=True):
    if not request.user.is_authenticated:
        return False
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if profile.role != Profile.Role.PRODUCER:
        return False
    if verified_only and not profile.is_verified:
        return False
    return True


def _producer_access_denied_response(request):
    if request.user.is_authenticated:
        profile, _ = Profile.objects.get_or_create(user=request.user)
        if profile.role == Profile.Role.PRODUCER and not profile.is_verified:
            messages.warning(
                request,
                "Your producer account is under review. Producer features will be available once an admin approves your account."
            )
            return redirect("home")
    return HttpResponseForbidden("Producer access only.")


# ----------------------------
# PRODUCT LIST
# ----------------------------

def product_list(request):
    expire_surplus_deals()

    products = (
        Product.objects.filter(is_active=True, stock_quantity__gt=0)
        .select_related("category", "producer")
        .prefetch_related("allergens")
    )

    categories = Category.objects.order_by("name")
    allergens = Allergen.objects.exclude(name="No common allergens").order_by("name")

    selected_category = request.GET.get("category", "").strip()
    selected_seasons = request.GET.getlist("season")
    query = request.GET.get("q", "").strip()
    selected_allergens = request.GET.getlist("allergens_exclude")
    organic_filter = request.GET.get("organic", "").strip()
    max_miles = request.GET.get("max_miles", "20").strip()

    try:
        max_miles = float(max_miles)
    except ValueError:
        max_miles = 20.0

    if selected_category:
        products = products.filter(category_id=selected_category)

    if selected_seasons:
        products = products.filter(season__in=selected_seasons)

    if selected_allergens:
        products = products.exclude(allergens__id__in=selected_allergens).distinct()

    if organic_filter == "certified":
        products = products.filter(is_organic=True)

    if query:
        from thefuzz import fuzz
        exact_matches = products.filter(
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(allergens__name__icontains=query) |
            Q(other_allergen_info__icontains=query) |
            Q(producer__username__icontains=query)
        ).distinct()

        if not exact_matches:
            all_products = list(products)
            fuzzy_matches = [
                p for p in all_products
                if fuzz.partial_ratio(query.lower(), p.name.lower()) >= 70
                or fuzz.partial_ratio(query.lower(), p.description.lower()) >= 70
            ]
            products = fuzzy_matches
        else:
            products = exact_matches

    today = date.today()
    current_month = today.month
    products = [p for p in products if p.is_in_season(today)]

    can_toggle_favourite_producers = (
        request.user.is_authenticated
        and getattr(request.user, "profile", None)
        and request.user.profile.role in BUYER_ROLES
    )
    favourite_producer_ids = set()
    if can_toggle_favourite_producers:
        favourite_producer_ids = set(
            FavouriteProducer.objects.filter(customer=request.user)
            .values_list("producer_id", flat=True)
        )

    for p in products:
        p.in_season_now = p.is_in_season(today)
        p.real_allergens = [a for a in p.allergens.all() if a.name != "No common allergens"]
        p.is_favourite_producer = p.producer_id in favourite_producer_ids

    # Food miles
    if request.user.is_authenticated:
        from .foodmiles import _get_lat_lng, _haversine_miles
        try:
            customer_profile = Profile.objects.get(user=request.user)
            customer_postcode = customer_profile.delivery_postcode or customer_profile.postcode
            customer_coords = _get_lat_lng(customer_postcode) if customer_postcode else None
        except Profile.DoesNotExist:
            customer_coords = None

        producer_coords_cache = {}

        for p in products:
            p.food_miles = None
            if customer_coords:
                try:
                    producer_profile = Profile.objects.get(user=p.producer)
                    producer_postcode = producer_profile.postcode
                    if producer_postcode:
                        if producer_postcode not in producer_coords_cache:
                            producer_coords_cache[producer_postcode] = _get_lat_lng(producer_postcode)
                        coords = producer_coords_cache[producer_postcode]
                        if coords:
                            p.food_miles = _haversine_miles(*customer_coords, *coords)
                except Profile.DoesNotExist:
                    pass

        products = [p for p in products if p.food_miles is None or p.food_miles <= max_miles]
    else:
        for p in products:
            p.food_miles = None

    context = {
        "products": products,
        "categories": categories,
        "allergens": allergens,
        "selected_allergens": selected_allergens,
        "selected_category": selected_category,
        "selected_seasons": selected_seasons,
        "seasons": Product.SEASON_CHOICES,
        "query": query,
        "current_month": current_month,
        "organic_filter": organic_filter,
        "max_miles": max_miles,
        "can_toggle_favourite_producers": can_toggle_favourite_producers,
    }

    return render(request, "marketplace/product_list.html", context)


# ----------------------------
# PRODUCT SEARCH SUGGESTIONS
# ----------------------------

def product_search_suggestions(request):
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"suggestions": []})

    from thefuzz import fuzz
    products = Product.objects.filter(is_active=True, stock_quantity__gt=0)
    exact = list(products.filter(name__icontains=query).values_list("name", flat=True)[:5])
    if exact:
        return JsonResponse({"suggestions": exact})

    all_products = list(products.exclude(name__icontains=query))
    fuzzy = [
        p.name for p in all_products
        if fuzz.partial_ratio(query.lower(), p.name.lower()) >= 75
    ][:5]
    return JsonResponse({"suggestions": fuzzy})


# ----------------------------
# PRODUCT DETAIL
# ----------------------------

def product_detail(request, pk):
    expire_surplus_deals()

    product = get_object_or_404(
        Product.objects.select_related("category", "producer").prefetch_related("allergens"),
        pk=pk,
        is_active=True,
    )

    if not product.is_active or product.stock_quantity <= 0 or not product.is_in_season():
        if request.user != product.producer:
            raise Http404("This product is not currently available.")

    reviews = Review.objects.filter(product=product).order_by("-id")
    average_rating = None
    if reviews.exists():
        average_rating = round(sum(review.rating for review in reviews) / reviews.count(), 1)

    food_miles = None
    is_favourite_producer = False
    can_favourite_producer = False
    if request.user.is_authenticated:
        from .foodmiles import calculate_food_miles
        try:
            customer_profile = Profile.objects.get(user=request.user)
            producer_profile = Profile.objects.get(user=product.producer)
            customer_postcode = customer_profile.delivery_postcode or customer_profile.postcode
            producer_postcode = producer_profile.postcode
            if customer_postcode and producer_postcode:
                food_miles = calculate_food_miles(customer_postcode, producer_postcode)
        except Profile.DoesNotExist:
            pass

        if getattr(request.user, "profile", None) and request.user.profile.role in BUYER_ROLES:
            can_favourite_producer = True
            is_favourite_producer = FavouriteProducer.objects.filter(
                customer=request.user,
                producer=product.producer,
            ).exists()

    return render(request, "marketplace/product_detail.html", {
        "product": product,
        "is_in_season": product.is_in_season(),
        "reviews": reviews,
        "average_rating": average_rating,
        "food_miles": food_miles,
        "can_favourite_producer": can_favourite_producer,
        "is_favourite_producer": is_favourite_producer,
    })


# ----------------------------
# SURPLUS DEALS
# ----------------------------

def surplus_deals(request):
    expire_surplus_deals()
    now = timezone.now()
    can_filter_favourite_offers = (
        request.user.is_authenticated
        and getattr(request.user, "profile", None)
        and request.user.profile.role in BUYER_ROLES
    )
    showing_favourites_only = (
        can_filter_favourite_offers and request.GET.get("favourites") == "1"
    )

    products = (
        Product.objects.filter(
            is_active=True, is_surplus=True,
            surplus_stock_quantity__gt=0, surplus_expires_at__gt=now, stock_quantity__gt=0,
        )
        .select_related("category", "producer")
        .prefetch_related("allergens")
        .order_by("surplus_expires_at")
    )
    today = date.today()

    if showing_favourites_only:
        favourite_producer_ids = FavouriteProducer.objects.filter(
            customer=request.user
        ).values_list("producer_id", flat=True)
        products = products.filter(producer_id__in=favourite_producer_ids)
        SurplusDealNotification.objects.filter(
            customer=request.user,
            is_read=False,
        ).update(is_read=True)

    products = [p for p in products if p.is_in_season(today)]
    favourite_producer_ids = set()
    if can_filter_favourite_offers:
        favourite_producer_ids = set(
            FavouriteProducer.objects.filter(customer=request.user)
            .values_list("producer_id", flat=True)
        )
    for p in products:
        p.in_season_now = p.is_in_season(today)
        p.is_favourite_producer = p.producer_id in favourite_producer_ids

    return render(
        request,
        "marketplace/surplus_deals.html",
        {
            "products": products,
            "showing_favourites_only": showing_favourites_only,
            "can_filter_favourite_offers": can_filter_favourite_offers,
        },
    )


@login_required
def surplus_notifications(request):
    if (
        not getattr(request.user, "profile", None)
        or request.user.profile.role not in BUYER_ROLES
    ):
        return HttpResponseForbidden("Buyer access only.")

    notifications = (
        SurplusDealNotification.objects
        .filter(customer=request.user)
        .select_related("producer", "product")
    )

    unread_ids = list(
        notifications.filter(is_read=False).values_list("id", flat=True)
    )
    if unread_ids:
        SurplusDealNotification.objects.filter(id__in=unread_ids).update(is_read=True)
        notifications = notifications.order_by("-created_at")

    return render(
        request,
        "marketplace/surplus_notifications.html",
        {"notifications": notifications},
    )


@login_required
def toggle_favourite_producer(request, producer_id):
    if (
        not getattr(request.user, "profile", None)
        or request.user.profile.role not in BUYER_ROLES
    ):
        return HttpResponseForbidden("Buyer access only.")

    producer = get_object_or_404(get_user_model(), pk=producer_id)

    if request.user == producer:
        messages.error(request, "You cannot favourite your own producer account.")
        return redirect(request.POST.get("next") or "marketplace:product_list")

    favourite, created = FavouriteProducer.objects.get_or_create(
        customer=request.user,
        producer=producer,
    )

    if created:
        messages.success(request, f"{producer.username} added to your favourite producers.")
    else:
        SurplusDealNotification.objects.filter(
            customer=request.user,
            producer=producer,
        ).delete()
        favourite.delete()
        messages.info(request, f"{producer.username} removed from your favourite producers.")

    return redirect(request.POST.get("next") or "marketplace:product_list")


# -----------------------------
# REVIEWS
# -----------------------------

@login_required
def create_review(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    order_history = _get_customer_order_history(request.user)
    has_purchased = _has_purchased_product(order_history, product_id)

    if not has_purchased:
        messages.error(request, "You can only review products you have purchased.")
        return redirect("marketplace:product_detail", pk=product_id)

    existing_review = Review.objects.filter(product=product, customer=request.user).first()
    if existing_review:
        messages.error(request, "You have already reviewed this product.")
        return redirect("marketplace:product_detail", pk=product_id)

    if request.method == "POST":
        form = ReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.product = product
            review.customer = request.user
            review.verified_purchase = True
            review.save()

            messages.success(request, "Product review submitted successfully.")
            return redirect("marketplace:product_detail", pk=product_id)
    else:
        form = ReviewForm()

    return render(request, "marketplace/review_form.html", {"form": form, "product": product})


# -----------------------------
# PRODUCER PRODUCT MANAGEMENT
# -----------------------------

@login_required
def producer_product_list(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    products = (
        Product.objects
        .filter(producer=request.user)
        .select_related("category")
        .order_by("-created_at")
    )

    active_alerts_count = StockNotification.objects.filter(
        producer=request.user, is_resolved=False
    ).count()

    return render(request, "marketplace/producer_product_list.html", {
        "products": products,
        "active_alerts_count": active_alerts_count,
    })


@login_required
def product_create(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES)
        if form.is_valid():
            product = form.save(commit=False)
            product.producer = request.user
            product.save()
            form.save_m2m()
            if product.is_active_surplus_deal:
                notify_favourite_customers_about_surplus(product)
            messages.success(request, "Product created.")
            return redirect("marketplace:producer_product_list")
    else:
        form = ProductForm()

    return render(request, "marketplace/product_form.html", {"form": form, "mode": "create"})


@login_required
def product_update(request, pk):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    product = get_object_or_404(Product, pk=pk, producer=request.user)

    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            product = form.save(commit=False)
            product.producer = request.user
            product.save()
            form.save_m2m()
            product.check_low_stock()
            if product.is_active_surplus_deal:
                notify_favourite_customers_about_surplus(product)
            messages.success(request, "Product updated.")
            return redirect("marketplace:producer_product_list")
    else:
        form = ProductForm(instance=product)

    return render(request, "marketplace/product_form.html", {"form": form, "mode": "edit"})


@login_required
def product_delete(request, pk):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    product = get_object_or_404(Product, pk=pk, producer=request.user)

    if request.method == "POST":
        product.delete()
        messages.success(request, "Product deleted.")
        return redirect("marketplace:producer_product_list")

    return render(request, "marketplace/product_confirm_delete.html", {"product": product})


# ----------------------------
# CHECKOUT
# ----------------------------

@login_required
def checkout(request):
    cart, _ = Cart.objects.get_or_create(user=request.user)
    cart_items = CartItem.objects.select_related("product", "product__producer").filter(cart=cart)

    producers = defaultdict(list)
    subtotal = Decimal("0.00")

    for cart_item in cart_items:
        product = cart_item.product
        if not product.is_active or product.stock_quantity <= 0:
            continue
        qty = int(cart_item.quantity)
        unit_price = _get_product_unit_price(product)
        line_total = unit_price * qty
        subtotal += line_total

        lead_time = getattr(product.producer, "lead_time", None)
        if lead_time is None and hasattr(product.producer, "profile"):
            lead_time = getattr(product.producer.profile, "lead_time", 2)
        if lead_time is None:
            lead_time = 2

        producers[product.producer.username].append({
            "name": product.name,
            "price": float(unit_price),
            "qty": qty,
            "total": float(line_total),
            "lead_time": lead_time,
            "id": product.id,
        })

    commission = (subtotal * COMMISSION_RATE).quantize(TWO_PLACES)
    total = (subtotal + commission).quantize(TWO_PLACES)

    if request.method == "POST":
        form = CheckoutForm(request.POST)
        print("CHECKOUT POST:", request.POST)
        print("FORM ERRORS:", form.errors)

        if form.is_valid():
            delivery_address = form.cleaned_data["delivery_address"]
            delivery_date = request.POST.get("delivery_1")
            payment_method = request.POST.get("payment_method")

            if not delivery_date:
                messages.error(request, "Please select a delivery date.")
                return render(request, "cart/checkout.html", {
                    "form": form, "producers": dict(producers),
                    "subtotal": subtotal, "commission": commission,
                    "total": total, "cart_items": cart_items,
                })

            if not payment_method:
                messages.error(request, "Please select a payment method.")
                return render(request, "cart/checkout.html", {
                    "form": form, "producers": dict(producers),
                    "subtotal": subtotal, "commission": commission,
                    "total": total, "cart_items": cart_items,
                })

            request.session["order"] = {
                "address": delivery_address,
                "date": str(delivery_date),
                "payment": payment_method,
                "subtotal": round(float(subtotal), 2),
                "commission": round(float(commission), 2),
                "total": round(float(total), 2),
                "producers": dict(producers),
            }
            request.session.modified = True
            return redirect("marketplace:payment")

        messages.error(request, "Please check the checkout form and try again.")
    else:
        initial = {"delivery_address": request.user.email or request.user.username}
        form = CheckoutForm(initial=initial)

    return render(request, "cart/checkout.html", {
        "form": form,
        "producers": dict(producers),
        "subtotal": float(subtotal),
        "commission": float(commission),
        "total": float(total),
        "cart_items": cart_items,
    })


# ----------------------------
# PAYMENT
# ----------------------------

@login_required
def payment(request):
    order = request.session.get("order")
    if not order:
        return redirect("marketplace:product_list")

    payment_method = str(order.get("payment", "")).strip().lower()
    if payment_method == "paypal":
        order["payment"] = "paypal_sandbox"
        request.session["order"] = order
        request.session.modified = True
        payment_method = "paypal_sandbox"

    is_paypal_sandbox = payment_method == "paypal_sandbox"
    debug_info = []

    if request.method == "POST":
        if is_paypal_sandbox:
            paypal_order_id = request.POST.get("paypal_order_id", "").strip()
            paypal_payer_id = request.POST.get("paypal_payer_id", "").strip()

            if not paypal_order_id or not paypal_payer_id:
                messages.error(
                    request,
                    "PayPal Sandbox approval was not completed. Please try again.",
                )
                return render(
                    request,
                    "orders/payment.html",
                    _payment_template_context(order),
                )
        else:
            card_number = request.POST.get("card_number", "").strip()
            expiry = request.POST.get("expiry", "").strip()
            cvc = request.POST.get("cvc", "").strip()

            if len(expiry) != 5 or expiry[2] != "/":
                messages.error(request, "Enter expiry date in MM/YY format.")
                return render(
                    request,
                    "orders/payment.html",
                    _payment_template_context(order),
                )

            month_part, year_part = expiry.split("/")
            if not month_part.isdigit() or not year_part.isdigit():
                messages.error(request, "Enter expiry date in MM/YY format.")
                return render(
                    request,
                    "orders/payment.html",
                    _payment_template_context(order),
                )

            month = int(month_part)
            year = int(year_part)

            if month < 1 or month > 12:
                messages.error(request, "Enter a valid expiry month.")
                return render(
                    request,
                    "orders/payment.html",
                    _payment_template_context(order),
                )

            today = timezone.localdate()
            current_year = today.year % 100
            if year < current_year or (year == current_year and month < today.month):
                messages.error(request, "Card expiry date cannot be in the past.")
                return render(
                    request,
                    "orders/payment.html",
                    _payment_template_context(order),
                )

        order_number = "ORD-" + str(random.randint(10000, 99999))
        delivery_date_obj = _parse_date(order.get("date"))

        try:
            User = get_user_model()
            customer = request.user

            if not customer.is_authenticated:
                raise Exception("User is not authenticated.")

            db_order = Order.objects.create(
                customer=customer,
                delivery_address=order["address"],
                delivery_postcode="",
                special_instructions="",
                total_amount=Decimal(str(order["total"])).quantize(TWO_PLACES),
            )

            debug_info.append(f"Created Order: {db_order}")

            for producer_username, items in order["producers"].items():
                producer = User.objects.get(username=producer_username)

                producer_order = ProducerOrder.objects.create(
                    order=db_order,
                    producer=producer,
                    delivery_date=delivery_date_obj,
                    status=ProducerOrder.Status.PENDING,
                    total_value=Decimal("0.00"),
                )

                total_value = Decimal("0.00")

                for item in items:
                    product = Product.objects.get(id=item["id"], producer=producer)
                    quantity = int(item["qty"])
                    unit_price = Decimal(str(item["price"]))
                    current_surplus_stock = int(product.surplus_stock_quantity or 0)
                    is_surplus_before_purchase = product.is_active_surplus_deal
                    surplus_quantity = 0
                    if is_surplus_before_purchase:
                        surplus_quantity = min(quantity, current_surplus_stock)

                    OrderItem.objects.create(
                        producer_order=producer_order,
                        product=product,
                        quantity=quantity,
                        unit_price=unit_price,
                    )

                    line_total = (unit_price * quantity).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    total_value += line_total

                    if surplus_quantity > 0:
                        normal_value = (product.price * surplus_quantity).quantize(
                            TWO_PLACES, rounding=ROUND_HALF_UP
                        )
                        discounted_value = (unit_price * surplus_quantity).quantize(
                            TWO_PLACES, rounding=ROUND_HALF_UP
                        )
                        customer_saving = (normal_value - discounted_value).quantize(
                            TWO_PLACES, rounding=ROUND_HALF_UP
                        )
                        estimated_weight_kg = (
                            product.estimated_unit_weight_kg * Decimal(surplus_quantity)
                        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

                        SurplusAnalyticsRecord.objects.create(
                            producer=producer,
                            product=product,
                            order=db_order,
                            record_type=SurplusAnalyticsRecord.RecordType.SAVED,
                            quantity=surplus_quantity,
                            estimated_weight_kg=estimated_weight_kg,
                            customer_saving=customer_saving,
                            revenue=discounted_value,
                        )

                    product.stock_quantity = max(0, product.stock_quantity - quantity)
                    if surplus_quantity > 0:
                        product.surplus_stock_quantity = max(
                            0, current_surplus_stock - surplus_quantity
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
                    product.save()
                    product.check_low_stock()

                producer_order.total_value = Decimal(str(total_value)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                debug_info.append(f"Updated stock for {product.name}: {product.stock_quantity}")
                producer_order.save()

        except Exception as error:
            import traceback
            error_message = f"Order creation failed: {error}"
            print(error_message)
            print(traceback.format_exc())
            return render(
                request,
                "orders/payment.html",
                _payment_template_context(
                    order,
                    error_message=error_message,
                    debug_info=debug_info,
                ),
            )

        order_data = {
            "order_number": order_number,
            "address": order["address"],
            "order_date": timezone.now().strftime("%Y-%m-%d"),
            "delivery_date": order["date"],
            "payment": order["payment"],
            "subtotal": order["subtotal"],
            "commission": order["commission"],
            "total": order["total"],
            "producers": order["producers"],
        }

        order_history = request.session.get("order_history", [])
        order_history.append(order_data)
        request.session["order_history"] = order_history
        request.session.modified = True

        CustomerOrderHistory.objects.create(
            customer=request.user,
            order_number=order_number,
            order_data=order_data,
        )

        cart, _ = Cart.objects.get_or_create(user=request.user)
        CartItem.objects.filter(cart=cart).delete()
        request.session.pop("order", None)
        request.session.pop("cart", None)

        return render(request, "orders/confirmation.html", {
            "order_number": order_number,
            "address": order["address"],
            "date": order["date"],
            "payment": order["payment"],
            "subtotal": order["subtotal"],
            "commission": order["commission"],
            "total": order["total"],
            "producers": order["producers"],
        })

    return render(request, "orders/payment.html", _payment_template_context(order))


# ----------------------------
# ALLERGEN TEST
# ----------------------------

def allergen_test(request):
    form = ProductForm()
    return render(request, "marketplace/allergen_test.html", {"form": form})


# -----------------------------
# PRODUCER ORDERS
# -----------------------------

@login_required
def producer_order_list(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    orders = (
        ProducerOrder.objects
        .filter(producer=request.user)
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product")
    )

    status = request.GET.get("status", "")
    if status:
        orders = orders.filter(status=status)

    sort = request.GET.get("sort", "newest")
    if sort == "oldest":
        orders = orders.order_by("order__created_at", "id")
    else:
        orders = orders.order_by("-order__created_at", "-id")

    orders = list(orders)
    _attach_producer_order_context(orders)

    return render(request, "marketplace/producer_order_list.html", {
        "orders": orders,
        "selected_status": status,
        "sort": sort,
        "status_choices": ProducerOrder.Status.choices,
    })


@login_required
def producer_order_detail(request, pk):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    producer_order = get_object_or_404(
        ProducerOrder.objects
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product"),
        pk=pk,
        producer=request.user,
    )

    _attach_producer_order_context([producer_order])

    return render(request, "marketplace/producer_order_detail.html", {"po": producer_order})


@login_required
def producer_order_update_status(request, pk):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    po = get_object_or_404(
        ProducerOrder.objects.select_related("order", "order__customer"),
        pk=pk, producer=request.user,
    )

    allowed_transitions = {
        ProducerOrder.Status.PENDING: [ProducerOrder.Status.CONFIRMED, ProducerOrder.Status.CANCELLED],
        ProducerOrder.Status.CONFIRMED: [ProducerOrder.Status.READY, ProducerOrder.Status.CANCELLED],
        ProducerOrder.Status.READY: [ProducerOrder.Status.DELIVERED],
        ProducerOrder.Status.DELIVERED: [],
        ProducerOrder.Status.CANCELLED: [],
    }

    next_statuses = allowed_transitions.get(po.status, [])
    status_choices = [(s, ProducerOrder.Status(s).label) for s in next_statuses]

    if request.method == "POST":
        form = ProducerOrderStatusForm(request.POST, status_choices=status_choices)
        if form.is_valid():
            new_status = form.cleaned_data["status"]
            note = form.cleaned_data["note"]
            try:
                update_producer_order_status(
                    producer_order=po, new_status=new_status,
                    changed_by=request.user, note=note,
                )
            except ValueError:
                messages.error(request, "Invalid status progression.")
                return redirect("marketplace:producer_order_detail", pk=po.pk)
            messages.success(request, "Order status updated successfully.")
            return redirect("marketplace:producer_order_detail", pk=po.pk)
    else:
        form = ProducerOrderStatusForm(initial={"status": po.status}, status_choices=status_choices)

    return render(request, "marketplace/producer_order_update_status.html", {"po": po, "form": form})


@login_required
def producer_order_management(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    current_orders = (
        ProducerOrder.objects
        .filter(producer=request.user, status__in=[
            ProducerOrder.Status.PENDING,
            ProducerOrder.Status.CONFIRMED,
            ProducerOrder.Status.READY,
        ])
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product", "status_history")
        .order_by("delivery_date", "-id")
    )

    order_history = (
        ProducerOrder.objects
        .filter(producer=request.user, status=ProducerOrder.Status.DELIVERED)
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product", "status_history")
        .order_by("-delivery_date", "-id")
    )

    return render(request, "marketplace/order_management.html", {
        "current_orders": current_orders,
        "order_history": order_history,
    })


# -----------------------------
# PRODUCER PAYMENTS
# -----------------------------

@login_required
def producer_payments(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    producer = request.user
    today = timezone.localdate()
    week_start, week_end = _producer_settlement_week_range(producer, today)
    tax_year_start = _uk_tax_year_start(today)

    all_delivered_orders = (
        ProducerOrder.objects
        .filter(producer=producer, status=ProducerOrder.Status.DELIVERED)
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product")
        .order_by("-delivery_date", "-id")
    )

    weekly_orders = all_delivered_orders.filter(
        delivery_date__range=(week_start, week_end)
    ).order_by("-delivery_date", "-id")

    gross_total = sum(
        (order.total_value or Decimal("0.00") for order in weekly_orders), Decimal("0.00"),
    ).quantize(TWO_PLACES)

    commission, net_payment = _compute_financials(gross_total)

    tax_year_orders = all_delivered_orders.filter(delivery_date__gte=tax_year_start)
    tax_year_total = sum(
        (order.total_value or Decimal("0.00") for order in tax_year_orders), Decimal("0.00"),
    ).quantize(TWO_PLACES)

    settlement_reference = _build_settlement_ref(producer.id, week_start, week_end)

    history_map = defaultdict(list)
    for order in all_delivered_orders:
        if not order.delivery_date:
            continue
        order_week_start = order.delivery_date - timedelta(days=order.delivery_date.weekday())
        order_week_end = order_week_start + timedelta(days=6)
        history_map[(order_week_start, order_week_end)].append(order)

    historical_records = []
    for (hist_start, hist_end), orders_in_week in sorted(history_map.items(), reverse=True):
        if hist_start == week_start and hist_end == week_end:
            continue
        hist_gross = sum(
            (order.total_value or Decimal("0.00") for order in orders_in_week), Decimal("0.00"),
        ).quantize(TWO_PLACES)
        hist_commission, hist_net = _compute_financials(hist_gross)
        historical_records.append({
            "week_start": hist_start, "week_end": hist_end,
            "gross": hist_gross, "commission": hist_commission,
            "net": hist_net, "order_count": len(orders_in_week),
        })

    return render(request, "marketplace/producer_payments.html", {
        "orders": weekly_orders,
        "gross_total": gross_total,
        "commission": commission,
        "net_payment": net_payment,
        "payment_status": "Pending Bank Transfer",
        "settlement_reference": settlement_reference,
        "tax_year_total": tax_year_total,
        "week_start": week_start,
        "week_end": week_end,
        "historical_records": historical_records,
    })


@login_required
def producer_surplus_impact(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    producer = request.user
    records = (
        SurplusAnalyticsRecord.objects
        .filter(producer=producer)
        .select_related("product", "order")
    )

    totals = records.aggregate(
        total_saved_items=Coalesce(
            Sum(
                "quantity",
                filter=Q(record_type=SurplusAnalyticsRecord.RecordType.SAVED),
            ),
            Value(0),
        ),
        total_saved_kg=Coalesce(
            Sum(
                "estimated_weight_kg",
                filter=Q(record_type=SurplusAnalyticsRecord.RecordType.SAVED),
            ),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        ),
        total_unsold_items=Coalesce(
            Sum(
                "quantity",
                filter=Q(record_type=SurplusAnalyticsRecord.RecordType.UNSOLD),
            ),
            Value(0),
        ),
        total_unsold_kg=Coalesce(
            Sum(
                "estimated_weight_kg",
                filter=Q(record_type=SurplusAnalyticsRecord.RecordType.UNSOLD),
            ),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        ),
        total_customer_saving=Coalesce(
            Sum(
                "customer_saving",
                filter=Q(record_type=SurplusAnalyticsRecord.RecordType.SAVED),
            ),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        ),
        total_surplus_revenue=Coalesce(
            Sum(
                "revenue",
                filter=Q(record_type=SurplusAnalyticsRecord.RecordType.SAVED),
            ),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        ),
    )

    weekly_records = (
        records.annotate(week=TruncWeek("created_at"))
        .values("week")
        .annotate(
            saved_kg=Coalesce(
                Sum(
                    "estimated_weight_kg",
                    filter=Q(record_type=SurplusAnalyticsRecord.RecordType.SAVED),
                ),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
            unsold_kg=Coalesce(
                Sum(
                    "estimated_weight_kg",
                    filter=Q(record_type=SurplusAnalyticsRecord.RecordType.UNSOLD),
                ),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )
        .order_by("week")
    )

    chart_labels = []
    saved_kg_data = []
    unsold_kg_data = []
    for record in weekly_records:
        week_value = record["week"]
        chart_labels.append(week_value.strftime("%b %d, %Y") if week_value else "Unknown")
        saved_kg_data.append(float(record["saved_kg"]))
        unsold_kg_data.append(float(record["unsold_kg"]))

    product_breakdown = (
        records.values("product__name")
        .annotate(
            saved_items=Coalesce(
                Sum(
                    "quantity",
                    filter=Q(record_type=SurplusAnalyticsRecord.RecordType.SAVED),
                ),
                Value(0),
            ),
            saved_kg=Coalesce(
                Sum(
                    "estimated_weight_kg",
                    filter=Q(record_type=SurplusAnalyticsRecord.RecordType.SAVED),
                ),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
            unsold_kg=Coalesce(
                Sum(
                    "estimated_weight_kg",
                    filter=Q(record_type=SurplusAnalyticsRecord.RecordType.UNSOLD),
                ),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
            customer_saving=Coalesce(
                Sum(
                    "customer_saving",
                    filter=Q(record_type=SurplusAnalyticsRecord.RecordType.SAVED),
                ),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
            revenue=Coalesce(
                Sum(
                    "revenue",
                    filter=Q(record_type=SurplusAnalyticsRecord.RecordType.SAVED),
                ),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )
        .order_by("product__name")
    )

    recent_records = records.order_by("-created_at", "-id")[:20]
    has_analytics_records = records.exists()
    has_chart_data = bool(chart_labels)
    needs_weight_setup = (
        has_analytics_records
        and (totals["total_saved_items"] or totals["total_unsold_items"])
        and totals["total_saved_kg"] == Decimal("0.00")
        and totals["total_unsold_kg"] == Decimal("0.00")
    )

    return render(
        request,
        "marketplace/producer_surplus_impact.html",
        {
            **totals,
            "chart_labels": json.dumps(chart_labels),
            "saved_kg_data": json.dumps(saved_kg_data),
            "unsold_kg_data": json.dumps(unsold_kg_data),
            "recent_records": recent_records,
            "product_breakdown": product_breakdown,
            "has_analytics_records": has_analytics_records,
            "has_chart_data": has_chart_data,
            "needs_weight_setup": needs_weight_setup,
        },
    )


@login_required
def download_payments_csv(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    producer = request.user
    today = timezone.localdate()
    week_start, week_end = _producer_settlement_week_range(producer, today)

    orders = (
        ProducerOrder.objects
        .filter(producer=producer, status=ProducerOrder.Status.DELIVERED, delivery_date__range=(week_start, week_end))
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product")
        .order_by("-delivery_date", "-id")
    )

    response = HttpResponse(content_type="text/csv")
    filename = f"payments_report_{week_start}_{week_end}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        "Settlement Reference", "Settlement Week Start", "Settlement Week End",
        "Producer", "Order Number", "Customer", "Delivery Date", "Items Sold",
        "Gross Amount", "Commission (5%)", "Net Payment", "Status",
    ])

    settlement_reference = _build_settlement_ref(producer.id, week_start, week_end)

    for order in orders:
        items_sold = ", ".join(f"{item.product.name} x{item.quantity}" for item in order.items.all())
        gross = (order.total_value or Decimal("0.00")).quantize(TWO_PLACES)
        commission, net = _compute_financials(gross)
        writer.writerow([
            settlement_reference, week_start, week_end,
            producer.username, order.order.id,
            _anonymise_customer(order.order.customer),
            order.delivery_date, items_sold, gross, commission, net,
            order.get_status_display(),
        ])

    return response


# -----------------------------
# Stock Notifications
# -----------------------------

@login_required
def stock_notifications(request):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    active_notifications = StockNotification.objects.filter(
        producer=request.user, is_resolved=False
    )
    resolved_notifications = StockNotification.objects.filter(
        producer=request.user, is_resolved=True
    )[:10]

    return render(request, "marketplace/stock_notifications.html", {
        "active_notifications": active_notifications,
        "resolved_notifications": resolved_notifications,
    })


# -----------------------------
# CUSTOMER ORDER HISTORY
# -----------------------------

@login_required
def order_history(request):
    orders = _get_customer_order_history(request.user)

    start = request.GET.get("start", "").strip()
    end = request.GET.get("end", "").strip()
    producer = request.GET.get("producer", "").strip()

    start_date = _parse_date(start)
    end_date = _parse_date(end)

    if start and not start_date:
        messages.error(request, "Invalid start date.")

    if end and not end_date:
        messages.error(request, "Invalid end date.")

    if start_date and end_date and end_date < start_date:
        messages.error(request, "End date cannot be before start date.")
    elif start_date or end_date:
        filtered_orders = []
        for order in orders:
            raw_order_date = order.get("order_date") or order.get("date")
            order_date = _parse_date(raw_order_date)
            if not order_date:
                continue
            if start_date and order_date < start_date:
                continue
            if end_date and order_date > end_date:
                continue
            filtered_orders.append(order)
        orders = filtered_orders

    if producer:
        orders = [
            order for order in orders
            if producer.lower() in [p.lower() for p in order.get("producers", {}).keys()]
        ]

    return render(request, "orders/history.html", {
        "orders": orders, "start": start, "end": end, "producer": producer,
    })


@login_required
def order_detail(request, order_id):
    orders = _get_customer_order_history(request.user)
    order = next((o for o in orders if str(o.get("order_number")) == str(order_id)), None)

    if not order:
        messages.error(request, "Order not found.")
        return redirect("marketplace:order_history")

    purchase_reviews = PurchaseReview.objects.filter(
        customer=request.user,
        order_number=order_id
    ).order_by("-created_at")

    return render(request, "orders/order_detail.html", {
        "order": order,
        "purchase_reviews": purchase_reviews,
    })


@login_required
def reorder(request, order_id):
    orders = _get_customer_order_history(request.user)
    order = next((o for o in orders if str(o.get("order_number")) == str(order_id)), None)

    if not order:
        messages.error(request, "Order not found.")
        return redirect("marketplace:order_history")

    cart, _ = Cart.objects.get_or_create(user=request.user)
    unavailable_items = []
    price_changed_items = []

    for producer, items in order.get("producers", {}).items():
        for item in items:
            try:
                product = Product.objects.get(id=item["id"], is_active=True)
            except Product.DoesNotExist:
                product = None

            if not product or product.stock_quantity <= 0:
                unavailable_items.append(item["name"])
                continue

            old_price = Decimal(str(item.get("price", product.price)))
            new_price = product.price
            if old_price != new_price:
                price_changed_items.append(f"{product.name}: was £{old_price}, now £{new_price}")

            cart_item, created = CartItem.objects.get_or_create(cart=cart, product=product)
            qty = int(item.get("qty", 1))
            if created:
                cart_item.quantity = qty
            else:
                cart_item.quantity += qty
            cart_item.save()

    if price_changed_items:
        messages.warning(request, "Price changes detected: " + "; ".join(price_changed_items))

    if unavailable_items:
        messages.error(request, "Some items unavailable: " + ", ".join(unavailable_items))

    messages.success(request, "Items added to cart with latest prices.")
    return redirect("cart:detail")


@login_required
def download_receipt(request, order_id):
    orders = _get_customer_order_history(request.user)
    order = next((o for o in orders if str(o.get("order_number")) == str(order_id)), None)

    if not order:
        messages.error(request, "Receipt not found.")
        return redirect("marketplace:order_history")

    content = f"Order {order['order_number']} - Total £{order['total']}"
    response = HttpResponse(content, content_type="text/plain")
    response["Content-Disposition"] = f'attachment; filename="receipt_{order_id}.txt"'
    return response


@login_required
def create_purchase_review(request, order_id):
    order_record = CustomerOrderHistory.objects.filter(
        customer=request.user,
        order_number=order_id
    ).first()

    if not order_record:
        messages.error(request, "Order not found.")
        return redirect("marketplace:order_history")

    if request.method == "POST":
        form = PurchaseReviewForm(request.POST)

        if form.is_valid():
            review = form.save(commit=False)
            review.customer = request.user
            review.order_number = order_id
            review.save()

            messages.success(request, "Purchase review submitted successfully.")
            return redirect("marketplace:order_detail", order_id=order_id)
    else:
        form = PurchaseReviewForm()

    return render(request, "marketplace/purchase_review_form.html", {
        "form": form,
        "order": order_record.order_data,
    })
