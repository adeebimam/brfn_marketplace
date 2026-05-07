import csv
import io
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.accounts.models import Profile
from apps.cart.models import Cart, CartItem

from .forms import (
    CheckoutForm,
    ProductForm,
    ProducerOrderStatusForm,
    PurchaseReviewForm,
    ReviewForm,
)
from .models import (
    Allergen,
    Category,
    CommissionLog,
    CustomerOrderHistory,
    MONTH_NAMES,
    Order,
    OrderItem,
    Product,
    ProducerOrder,
    ProducerOrderStatusHistory,
    PurchaseReview,
    RecurringNotification,
    RecurringOrder,
    RecurringOrderInstance,
    RecurringOrderInstanceItem,
    RecurringOrderItem,
    RefundRequest,
    Review,
    StockNotification,
)
from .services import expire_surplus_deals, update_producer_order_status


COMMISSION_RATE = Decimal("0.05")
TWO_PLACES = Decimal("0.01")


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


def _uk_tax_year_start(today):
    tax_year_start = date(today.year, 4, 6)
    if today < tax_year_start:
        tax_year_start = date(today.year - 1, 4, 6)
    return tax_year_start


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
    records = CustomerOrderHistory.objects.filter(customer=user).order_by("-id")
    result = []

    for record in records:
        data = record.order_data.copy()

        try:
            order_pk = str(record.order_number).replace("BRFN-", "").replace("ORD-", "")
            live_order = Order.objects.get(pk=order_pk)
            data["status"] = live_order.get_status_display()
        except (Order.DoesNotExist, ValueError):
            data["status"] = data.get("status", "Pending").title()

        for producer, items in data.get("producers", {}).items():
            for item in items:
                if "product_id" not in item:
                    try:
                        product = Product.objects.get(name=item.get("name"))
                        item["product_id"] = product.id
                    except Product.DoesNotExist:
                        item["product_id"] = None

        result.append(data)

    return result


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
                (producer_order.delivery_date - order_date).days,
                0,
            )
        else:
            producer_order.lead_time_days = 0

        producer_order.item_summary = ", ".join(
            f"{item.product.name} x{item.quantity}"
            for item in producer_order.items.all()
        )

    return producer_orders


def _get_product_suggestions(product, limit=4):
    suggestions = Product.objects.filter(
        is_active=True,
        stock_quantity__gt=0,
    ).exclude(id=product.id)

    if product.category:
        category_suggestions = suggestions.filter(category=product.category)[:limit]
        if category_suggestions.exists():
            return category_suggestions

    return suggestions[:limit]


def _normalize_order_number(order_id):
    order_str = str(order_id)
    if order_str.startswith("ORD-"):
        return order_str
    return f"ORD-{order_str}"


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
                "Your producer account is under review. Producer features will be available once an admin approves your account.",
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
            Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(allergens__name__icontains=query)
            | Q(other_allergen_info__icontains=query)
            | Q(producer__username__icontains=query)
        ).distinct()

        if not exact_matches:
            all_products = list(products)
            fuzzy_matches = [
                product for product in all_products
                if fuzz.partial_ratio(query.lower(), product.name.lower()) >= 70
                or fuzz.partial_ratio(query.lower(), product.description.lower()) >= 70
            ]
            products = fuzzy_matches
        else:
            products = exact_matches

    today = date.today()
    current_month = today.month
    products = [product for product in products if product.is_in_season(today)]

    for product in products:
        product.in_season_now = product.is_in_season(today)
        product.real_allergens = [
            allergen for allergen in product.allergens.all()
            if allergen.name != "No common allergens"
        ]

    if request.user.is_authenticated:
        from .foodmiles import _get_lat_lng, _haversine_miles

        try:
            customer_profile = Profile.objects.get(user=request.user)
            customer_postcode = customer_profile.delivery_postcode or customer_profile.postcode
            customer_coords = _get_lat_lng(customer_postcode) if customer_postcode else None
        except Profile.DoesNotExist:
            customer_coords = None

        producer_coords_cache = {}

        for product in products:
            product.food_miles = None

            if customer_coords:
                try:
                    producer_profile = Profile.objects.get(user=product.producer)
                    producer_postcode = producer_profile.postcode

                    if producer_postcode:
                        if producer_postcode not in producer_coords_cache:
                            producer_coords_cache[producer_postcode] = _get_lat_lng(producer_postcode)

                        coords = producer_coords_cache[producer_postcode]

                        if coords:
                            product.food_miles = _haversine_miles(*customer_coords, *coords)

                except Profile.DoesNotExist:
                    pass

        products = [
            product for product in products
            if product.food_miles is None or product.food_miles <= max_miles
        ]

    else:
        for product in products:
            product.food_miles = None

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
        "is_bulk_buyer": (
            hasattr(request.user, "profile")
            and request.user.profile.role in {"COMMUNITY_GROUP", "RESTAURANT"}
        ) if request.user.is_authenticated else False,
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
        product.name for product in all_products
        if fuzz.partial_ratio(query.lower(), product.name.lower()) >= 75
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

    suggestions = []
    if product.stock_quantity <= 0 or not product.is_in_season():
        suggestions = _get_product_suggestions(product)

    stock_limit = min(product.stock_quantity, 10)
    stock_range = range(1, stock_limit + 1)

    food_miles = None
    if request.user.is_authenticated:
        try:
            from .foodmiles import calculate_food_miles

            customer_profile = Profile.objects.get(user=request.user)
            producer_profile = Profile.objects.get(user=product.producer)
            customer_postcode = customer_profile.delivery_postcode or customer_profile.postcode
            producer_postcode = producer_profile.postcode

            if customer_postcode and producer_postcode:
                food_miles = calculate_food_miles(customer_postcode, producer_postcode)

        except (Profile.DoesNotExist, ImportError):
            food_miles = None

    return render(request, "marketplace/product_detail.html", {
        "product": product,
        "is_in_season": product.is_in_season(),
        "reviews": reviews,
        "average_rating": average_rating,
        "suggestions": suggestions,
        "stock_range": stock_range,
        "food_miles": food_miles,
        "is_bulk_buyer": (
            hasattr(request.user, "profile")
            and request.user.profile.role in {"COMMUNITY_GROUP", "RESTAURANT"}
        ) if request.user.is_authenticated else False,
    })


# ----------------------------
# SURPLUS DEALS
# ----------------------------

def surplus_deals(request):
    expire_surplus_deals()

    now = timezone.now()
    products = (
        Product.objects.filter(
            is_active=True,
            is_surplus=True,
            surplus_stock_quantity__gt=0,
            surplus_expires_at__gt=now,
            stock_quantity__gt=0,
        )
        .select_related("category", "producer")
        .prefetch_related("allergens")
        .order_by("surplus_expires_at")
    )

    today = date.today()
    products = [product for product in products if product.is_in_season(today)]

    for product in products:
        product.in_season_now = product.is_in_season(today)

    return render(request, "marketplace/surplus_deals.html", {"products": products})


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
        producer=request.user,
        is_resolved=False,
    ).count()

    return render(request, "marketplace/producer_product_list.html", {
        "products": products,
        "active_alerts_count": active_alerts_count,
    })


# ----------------------------
# PRODUCT CREATE / UPDATE / DELETE
# ----------------------------

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

        if form.is_valid():
            delivery_address = form.cleaned_data["delivery_address"]
            delivery_postcode = form.cleaned_data["delivery_postcode"]
            delivery_date = request.POST.get("delivery_1")
            payment_method = request.POST.get("payment_method")

            if not delivery_date:
                messages.error(request, "Please select a delivery date.")
                return render(request, "cart/checkout.html", {
                    "form": form,
                    "producers": dict(producers),
                    "subtotal": subtotal,
                    "commission": commission,
                    "total": total,
                    "cart_items": cart_items,
                })

            if not payment_method:
                messages.error(request, "Please select a payment method.")
                return render(request, "cart/checkout.html", {
                    "form": form,
                    "producers": dict(producers),
                    "subtotal": subtotal,
                    "commission": commission,
                    "total": total,
                    "cart_items": cart_items,
                })

            request.session["order"] = {
                "address": delivery_address,
                "delivery_postcode": delivery_postcode,
                "date": str(delivery_date),
                "payment": payment_method,
                "subtotal": round(float(subtotal), 2),
                "commission": round(float(commission), 2),
                "total": round(float(total), 2),
                "producers": dict(producers),
            }
            request.session.modified = True

            if request.POST.get("make_recurring"):
                frequency = request.POST.get("recurring_frequency", "WEEKLY")
                order_day = int(request.POST.get("recurring_order_day", 0))
                delivery_day = int(request.POST.get("recurring_delivery_day", 2))
                name = request.POST.get("recurring_name", "").strip()
                card_number = request.POST.get("card_number", "").replace(" ", "")
                card_expiry = request.POST.get("expiry", "").strip()
                card_last_four = card_number[-4:] if len(card_number) >= 4 else ""

                if delivery_day <= order_day:
                    messages.warning(
                        request,
                        "Recurring order: delivery day must be after order day. Defaulting to Wednesday.",
                    )
                    delivery_day = order_day + 2 if order_day + 2 <= 6 else 6

                recurring_order = RecurringOrder.objects.create(
                    customer=request.user,
                    frequency=frequency,
                    order_day=order_day,
                    delivery_day=delivery_day,
                    delivery_address=delivery_address,
                    delivery_postcode=delivery_postcode,
                    payment_method=payment_method,
                    name=name,
                    card_last_four=card_last_four,
                    card_expiry=card_expiry,
                )

                for producer_username, items in producers.items():
                    for item in items:
                        try:
                            product = Product.objects.get(id=item["id"])
                            RecurringOrderItem.objects.create(
                                recurring_order=recurring_order,
                                product=product,
                                quantity=item["qty"],
                                unit_price=Decimal(str(item["price"])),
                            )
                        except Product.DoesNotExist:
                            pass

                _generate_next_instance(recurring_order)

                order_label = recurring_order.name or f"Recurring order #{recurring_order.id}"
                _notify(
                    recipient=request.user,
                    notification_type=RecurringNotification.Type.ORDER_SETUP,
                    message=(
                        f"Your recurring order '{order_label}' has been set up. "
                        f"It processes every {recurring_order.get_frequency_display().lower()} "
                        f"on {dict(RecurringOrder.DAY_CHOICES)[recurring_order.order_day]}s. "
                        f"Next order date: {recurring_order.next_order_date}. "
                        f"Payment will be charged to card ending {card_last_four}."
                    ),
                    recurring_order=recurring_order,
                )

                producer_ids_notified = set()

                for producer_username, items in producers.items():
                    try:
                        User = get_user_model()
                        producer_user = User.objects.get(username=producer_username)

                        if producer_user.id not in producer_ids_notified:
                            product_names = ", ".join(item["name"] for item in items)
                            _notify(
                                recipient=producer_user,
                                notification_type=RecurringNotification.Type.PRODUCER_NOTICE,
                                message=(
                                    f"A customer has set up a recurring order including your products: {product_names}. "
                                    f"Expect a regular order every {recurring_order.get_frequency_display().lower()} "
                                    f"starting {recurring_order.next_order_date}."
                                ),
                                recurring_order=recurring_order,
                            )
                            producer_ids_notified.add(producer_user.id)

                    except Exception:
                        pass

                request.session["recurring_order_created"] = recurring_order.id
                messages.success(
                    request,
                    f"Recurring order set up! Next order: {recurring_order.next_order_date}",
                )

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

    debug_info = []

    if request.method == "POST":
        card_number = request.POST.get("card_number", "").strip()
        expiry = request.POST.get("expiry", "").strip()
        cvc = request.POST.get("cvc", "").strip()

        if len(expiry) != 5 or expiry[2] != "/":
            messages.error(request, "Enter expiry date in MM/YY format.")
            return render(request, "orders/payment.html", {"order": order})

        month_part, year_part = expiry.split("/")

        if not month_part.isdigit() or not year_part.isdigit():
            messages.error(request, "Enter expiry date in MM/YY format.")
            return render(request, "orders/payment.html", {"order": order})

        month = int(month_part)
        year = int(year_part)

        if month < 1 or month > 12:
            messages.error(request, "Enter a valid expiry month.")
            return render(request, "orders/payment.html", {"order": order})

        today = timezone.localdate()
        current_year = today.year % 100

        if year < current_year or (year == current_year and month < today.month):
            messages.error(request, "Card expiry date cannot be in the past.")
            return render(request, "orders/payment.html", {"order": order})

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
                delivery_postcode=order.get("delivery_postcode", ""),
                special_instructions="",
                total_amount=Decimal(str(order["total"])).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                ),
            )

            order_number = f"ORD-{db_order.id}"
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

                    OrderItem.objects.create(
                        producer_order=producer_order,
                        product=product,
                        quantity=quantity,
                        unit_price=unit_price,
                    )

                    total_value += unit_price * quantity
                    product.stock_quantity = max(0, product.stock_quantity - quantity)
                    product.save()

                producer_order.total_value = Decimal(str(total_value)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
                producer_order.save()

        except Exception as error:
            import traceback

            error_message = f"Order creation failed: {error}"
            print(error_message)
            print(traceback.format_exc())

            return render(request, "orders/payment.html", {
                "order": order,
                "error_message": error_message,
                "debug_info": debug_info,
            })

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

        recurring_id = request.session.pop("recurring_order_created", None)

        if recurring_id:
            try:
                recurring_order = RecurringOrder.objects.get(id=recurring_id)
                cleaned_card = card_number.replace(" ", "")
                card_last_four = cleaned_card[-4:] if len(cleaned_card) >= 4 else ""
                recurring_order.card_last_four = card_last_four
                recurring_order.card_expiry = expiry
                recurring_order.save(update_fields=["card_last_four", "card_expiry"])

            except RecurringOrder.DoesNotExist:
                pass

            return render(request, "orders/confirmation.html", {
                "order_number": order_number,
                "address": order["address"],
                "date": order["date"],
                "payment": order["payment"],
                "subtotal": order["subtotal"],
                "commission": order["commission"],
                "total": order["total"],
                "producers": order["producers"],
                "recurring_order_id": recurring_id,
            })

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

    return render(request, "orders/payment.html", {"order": order})


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

    order_type = request.GET.get("order_type", "")
    if order_type:
        orders = orders.filter(order_type=order_type)

    sort = request.GET.get("sort", "newest")
    if sort == "oldest":
        orders = orders.order_by("order__created_at", "id")
    else:
        orders = orders.order_by("-order__created_at", "-id")

    orders = list(orders)
    _attach_producer_order_context(orders)

    refund_requests = (
        RefundRequest.objects
        .filter(
            order__producer_orders__producer=request.user,
            status__in=[
                RefundRequest.Status.PENDING,
                RefundRequest.Status.PRODUCER_RESPONDED,
            ],
        )
        .select_related("order", "customer")
        .distinct()
    )

    return render(request, "marketplace/producer_order_list.html", {
        "orders": orders,
        "selected_status": status,
        "selected_order_type": order_type,
        "sort": sort,
        "status_choices": ProducerOrder.Status.choices,
        "order_type_choices": ProducerOrder.OrderType.choices,
        "refund_requests": refund_requests,
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
        pk=pk,
        producer=request.user,
    )

    allowed_transitions = {
        ProducerOrder.Status.PENDING: [
            ProducerOrder.Status.CONFIRMED,
            ProducerOrder.Status.CANCELLED,
        ],
        ProducerOrder.Status.CONFIRMED: [
            ProducerOrder.Status.READY,
            ProducerOrder.Status.CANCELLED,
        ],
        ProducerOrder.Status.READY: [ProducerOrder.Status.DELIVERED],
        ProducerOrder.Status.DELIVERED: [],
        ProducerOrder.Status.CANCELLED: [],
    }

    next_statuses = allowed_transitions.get(po.status, [])
    status_choices = [(status, ProducerOrder.Status(status).label) for status in next_statuses]

    if request.method == "POST":
        form = ProducerOrderStatusForm(request.POST, status_choices=status_choices)

        if form.is_valid():
            new_status = form.cleaned_data["status"]
            note = form.cleaned_data["note"]

            try:
                update_producer_order_status(
                    producer_order=po,
                    new_status=new_status,
                    changed_by=request.user,
                    note=note,
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
        .filter(
            producer=request.user,
            status__in=[
                ProducerOrder.Status.PENDING,
                ProducerOrder.Status.CONFIRMED,
                ProducerOrder.Status.READY,
            ],
        )
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
    week_start, week_end = _last_completed_week_range(today)
    tax_year_start = _uk_tax_year_start(today)

    all_delivered_orders = (
        ProducerOrder.objects
        .filter(producer=producer, status=ProducerOrder.Status.DELIVERED)
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product")
        .order_by("-delivery_date", "-id")
    )

    weekly_orders = all_delivered_orders.filter(
        delivery_date__range=(week_start, week_end),
    ).order_by("-delivery_date", "-id")

    gross_total = sum(
        (order.total_value or Decimal("0.00") for order in weekly_orders),
        Decimal("0.00"),
    ).quantize(TWO_PLACES)

    commission, net_payment = _compute_financials(gross_total)

    tax_year_orders = all_delivered_orders.filter(delivery_date__gte=tax_year_start)
    tax_year_total = sum(
        (order.total_value or Decimal("0.00") for order in tax_year_orders),
        Decimal("0.00"),
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
            (order.total_value or Decimal("0.00") for order in orders_in_week),
            Decimal("0.00"),
        ).quantize(TWO_PLACES)

        hist_commission, hist_net = _compute_financials(hist_gross)

        historical_records.append({
            "week_start": hist_start,
            "week_end": hist_end,
            "gross": hist_gross,
            "commission": hist_commission,
            "net": hist_net,
            "order_count": len(orders_in_week),
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
def download_payments_csv(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    producer = request.user
    today = timezone.localdate()
    week_start, week_end = _last_completed_week_range(today)

    orders = (
        ProducerOrder.objects
        .filter(
            producer=producer,
            status=ProducerOrder.Status.DELIVERED,
            delivery_date__range=(week_start, week_end),
        )
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product")
        .order_by("-delivery_date", "-id")
    )

    response = HttpResponse(content_type="text/csv")
    filename = f"payments_report_{week_start}_{week_end}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        "Settlement Reference",
        "Order Number",
        "Customer",
        "Delivery Date",
        "Items Sold",
        "Gross Amount",
        "Commission (5%)",
        "Net Payment",
        "Status",
    ])

    settlement_reference = _build_settlement_ref(producer.id, week_start, week_end)

    for order in orders:
        items_sold = ", ".join(
            f"{item.product.name} x{item.quantity}"
            for item in order.items.all()
        )
        gross = (order.total_value or Decimal("0.00")).quantize(TWO_PLACES)
        commission, net = _compute_financials(gross)

        writer.writerow([
            settlement_reference,
            order.order.id,
            _anonymise_customer(order.order.customer),
            order.delivery_date,
            items_sold,
            gross,
            commission,
            net,
            order.get_status_display(),
        ])

    return response


# -----------------------------
# STOCK NOTIFICATIONS
# -----------------------------

@login_required
def stock_notifications(request):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    active_notifications = StockNotification.objects.filter(
        producer=request.user,
        is_resolved=False,
    )

    resolved_notifications = StockNotification.objects.filter(
        producer=request.user,
        is_resolved=True,
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
    orders = (
        Order.objects
        .filter(customer=request.user)
        .prefetch_related(
            "producer_orders",
            "producer_orders__producer",
            "producer_orders__items",
            "producer_orders__items__product",
        )
        .order_by("-created_at")
    )

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
    else:
        if start_date:
            orders = orders.filter(created_at__date__gte=start_date)

        if end_date:
            orders = orders.filter(created_at__date__lte=end_date)

    if producer:
        orders = orders.filter(
            producer_orders__producer__username__icontains=producer,
        ).distinct()

    orders = list(orders)

    for order in orders:
        order.refund = RefundRequest.objects.filter(order=order).first()

    recurring_orders = (
        RecurringOrder.objects
        .filter(customer=request.user)
        .prefetch_related("items", "items__product")
        .order_by("-created_at")
    )

    return render(request, "orders/history.html", {
        "orders": orders,
        "start": start,
        "end": end,
        "producer": producer,
        "recurring_orders": recurring_orders,
    })


@login_required
def order_detail(request, order_id):
    clean_id = str(order_id).replace("ORD-", "").replace("BRFN-", "")

    order = get_object_or_404(
        Order.objects
        .filter(customer=request.user)
        .prefetch_related(
            "producer_orders",
            "producer_orders__producer",
            "producer_orders__items",
            "producer_orders__items__product",
        ),
        id=clean_id,
    )

    purchase_reviews = PurchaseReview.objects.filter(
        customer=request.user,
        order_number=f"ORD-{order.id}",
    ).order_by("-created_at")

    existing_refund = RefundRequest.objects.filter(order=order).first()

    return render(request, "orders/order_detail.html", {
        "order": order,
        "order_id": order.id,
        "purchase_reviews": purchase_reviews,
        "existing_refund": existing_refund,
    })


@login_required
def reorder(request, order_id):
    clean_id = str(order_id).replace("ORD-", "").replace("BRFN-", "")

    order = get_object_or_404(
        Order.objects
        .filter(customer=request.user)
        .prefetch_related("producer_orders__items__product"),
        id=clean_id,
    )

    cart, _ = Cart.objects.get_or_create(user=request.user)
    unavailable_items = []
    price_changed_items = []
    suggested_items = []

    for producer_order in order.producer_orders.all():
        for old_item in producer_order.items.all():
            product = old_item.product

            if not product.is_active or product.stock_quantity <= 0:
                unavailable_items.append(product.name)

                suggestions = Product.objects.filter(
                    category=product.category,
                    is_active=True,
                    stock_quantity__gt=0,
                ).exclude(id=product.id)[:3]

                for suggestion in suggestions:
                    suggested_items.append({
                        "original": product.name,
                        "id": suggestion.id,
                        "name": suggestion.name,
                        "producer": suggestion.producer.username,
                        "price": str(suggestion.price),
                    })

                continue

            old_price = old_item.unit_price
            new_price = product.price

            if old_price != new_price:
                price_changed_items.append(
                    f"{product.name}: was £{old_price}, now £{new_price}"
                )

            cart_item, created = CartItem.objects.get_or_create(
                cart=cart,
                product=product,
            )

            if created:
                cart_item.quantity = old_item.quantity
            else:
                cart_item.quantity += old_item.quantity

            cart_item.save()

    if suggested_items:
        request.session["reorder_suggestions"] = suggested_items
    else:
        request.session.pop("reorder_suggestions", None)

    request.session.modified = True

    if price_changed_items:
        messages.warning(request, "Price changes detected: " + "; ".join(price_changed_items))

    if unavailable_items:
        messages.error(request, "Some items are unavailable: " + ", ".join(unavailable_items))

    if suggested_items:
        messages.info(request, "Suggested alternatives are shown below.")

    messages.success(request, "Available items added to cart with latest prices.")
    return redirect("cart:detail")


@login_required
def download_receipt(request, order_id):
    clean_id = str(order_id).replace("ORD-", "").replace("BRFN-", "")

    try:
        order = (
            Order.objects
            .prefetch_related(
                "producer_orders",
                "producer_orders__producer",
                "producer_orders__items",
                "producer_orders__items__product",
            )
            .get(pk=clean_id, customer=request.user)
        )
    except Order.DoesNotExist:
        messages.error(request, "Receipt not found.")
        return redirect("marketplace:order_history")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("BRFN Marketplace", styles["Title"]))
    elements.append(Paragraph("Order Receipt", styles["Heading2"]))
    elements.append(Spacer(1, 0.5 * cm))

    info_data = [
        ["Order Number", f"#{order.id}"],
        ["Order Date", order.created_at.strftime("%Y-%m-%d")],
        ["Delivery Address", order.delivery_address],
        ["Status", order.get_status_display()],
        ["Total", f"£{order.total_amount}"],
    ]

    if order.special_instructions:
        info_data.append(["Special Instructions", order.special_instructions])

    info_table = Table(info_data, colWidths=[5 * cm, 11 * cm])
    info_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#f8fafc"), colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 0.5 * cm))

    elements.append(Paragraph("Order Items", styles["Heading3"]))
    elements.append(Spacer(1, 0.3 * cm))

    for producer_order in order.producer_orders.all():
        elements.append(Paragraph(f"Producer: {producer_order.producer.username}", styles["Heading4"]))

        item_data = [["Product", "Quantity", "Unit Price", "Total"]]

        for item in producer_order.items.all():
            line_total = (item.unit_price * item.quantity).quantize(Decimal("0.01"))
            item_data.append([
                item.product.name,
                str(item.quantity),
                f"£{item.unit_price}",
                f"£{line_total}",
            ])

        item_table = Table(item_data, colWidths=[7 * cm, 3 * cm, 3 * cm, 3 * cm])
        item_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#087d73")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("PADDING", (0, 0), (-1, -1), 6),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ]))
        elements.append(item_table)
        elements.append(Spacer(1, 0.3 * cm))

    elements.append(Spacer(1, 0.3 * cm))

    commission = (order.total_amount * Decimal("0.05")).quantize(Decimal("0.01"))

    totals_data = [
        ["Network Commission (5%)", f"£{commission}"],
        ["Total", f"£{order.total_amount}"],
    ]

    totals_table = Table(totals_data, colWidths=[13 * cm, 3 * cm])
    totals_table.setStyle(TableStyle([
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
        ("ROWBACKGROUNDS", (0, 0), (-1, -2), [colors.white]),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f0fdf4")),
    ]))
    elements.append(totals_table)

    doc.build(elements)
    buffer.seek(0)

    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="receipt_{order.id}.pdf"'
    return response


@login_required
def create_purchase_review(request, order_id):
    order_number = _normalize_order_number(order_id)

    order_record = CustomerOrderHistory.objects.filter(
        customer=request.user,
        order_number=order_number,
    ).first()

    if not order_record:
        messages.error(request, "Order not found.")
        return redirect("marketplace:order_history")

    existing = PurchaseReview.objects.filter(
        customer=request.user,
        order_number=order_number,
    ).first()

    if existing:
        messages.info(request, "You already reviewed this order.")
        return redirect("marketplace:order_history")

    if request.method == "POST":
        form = PurchaseReviewForm(request.POST)

        if form.is_valid():
            review = form.save(commit=False)
            review.customer = request.user
            review.order_number = order_number
            review.save()

            messages.success(request, "Review submitted successfully.")
            return redirect("marketplace:order_history")

    else:
        form = PurchaseReviewForm()

    return render(request, "marketplace/review_form.html", {
        "form": form,
        "order": order_record,
    })


def product_search_suggestions(request):
    query = request.GET.get("q", "").strip()

    if not query:
        return JsonResponse({"suggestions": []})

    products = Product.objects.filter(
        name__icontains=query,
        is_active=True,
        stock_quantity__gt=0,
    ).order_by("name")[:8]

    suggestions = [
        {
            "id": product.id,
            "name": product.name,
            "price": str(product.price),
        }
        for product in products
    ]

    return JsonResponse({"suggestions": suggestions})


# ─────────────────────────────────────────────
# REFUND REQUESTS
# ─────────────────────────────────────────────

@login_required
def request_refund(request, order_id):
    clean_id = str(order_id).replace("ORD-", "").replace("BRFN-", "")

    order = get_object_or_404(
        Order.objects.prefetch_related("producer_orders", "producer_orders__producer"),
        pk=clean_id,
        customer=request.user,
    )

    existing = RefundRequest.objects.filter(order=order).first()

    if existing:
        messages.warning(request, "A refund request already exists for this order.")
        return redirect("marketplace:order_detail", order_id=order_id)

    if order.status == Order.Status.CANCELLED:
        messages.error(request, "This order has already been cancelled.")
        return redirect("marketplace:order_detail", order_id=order_id)

    if order.status == Order.Status.DELIVERED:
        days_since_order = (timezone.now().date() - order.created_at.date()).days

        if days_since_order > 7:
            messages.error(
                request,
                "Refunds can only be requested within 7 days of delivery. "
                "This order is no longer eligible for a refund.",
            )
            return redirect("marketplace:order_detail", order_id=order_id)

    if order.status not in [
        Order.Status.PENDING,
        Order.Status.CONFIRMED,
        Order.Status.DELIVERED,
    ]:
        messages.error(request, "This order is not eligible for a refund.")
        return redirect("marketplace:order_detail", order_id=order_id)

    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()

        if not reason:
            messages.error(request, "Please provide a reason for the refund.")
        else:
            RefundRequest.objects.create(
                order=order,
                customer=request.user,
                reason=reason,
                refund_amount=order.total_amount,
                status=RefundRequest.Status.PENDING,
            )

            from apps.message.models import Message, MessageThread

            for producer_order in order.producer_orders.all():
                thread = MessageThread.objects.create(
                    subject=f"Refund Request for Order #{order.id}",
                    created_by=request.user,
                    related_order=order,
                    related_producer_order=producer_order,
                )
                thread.participants.add(request.user, producer_order.producer)

                Message.objects.create(
                    thread=thread,
                    sender=request.user,
                    body=(
                        f"Dear {producer_order.producer.username},\n\n"
                        f"A refund request has been raised for Order #{order.id}.\n\n"
                        f"Reason: {reason}\n\n"
                        f"Refund Amount: £{order.total_amount}\n\n"
                        f"Please log in to provide your response. "
                        f"The final decision will be made by an administrator.\n\n"
                        f"Thank you."
                    ),
                )

            messages.success(request, "Refund request submitted successfully.")
            return redirect("marketplace:order_detail", order_id=order_id)

    return render(request, "marketplace/refund_request_form.html", {
        "order": order,
        "order_obj": order,
    })


# =============================================================================
# RECURRING ORDERS
# =============================================================================

def _notify(recipient, notification_type, message, recurring_order=None):
    RecurringNotification.objects.create(
        recipient=recipient,
        recurring_order=recurring_order,
        notification_type=notification_type,
        message=message,
    )


def _simulate_charge(recurring_order, amount):
    if not recurring_order.card_last_four or not recurring_order.card_expiry:
        return False, "", "No saved card details on file."

    try:
        month_str, year_str = recurring_order.card_expiry.split("/")
        exp_month = int(month_str)
        exp_year = 2000 + int(year_str)
        today = date.today()

        if exp_year < today.year or (exp_year == today.year and exp_month < today.month):
            return False, "", f"Saved card ending {recurring_order.card_last_four} has expired."

    except (ValueError, AttributeError):
        return False, "", "Invalid card expiry format."

    reference = f"REC-PAY-{random.randint(100000, 999999)}"
    return True, reference, ""


def _delivery_date_respecting_lead_time(order_date, delivery_weekday, items):
    max_lead = 2

    for item in items:
        try:
            lead = getattr(item.product.producer, "lead_time", None)

            if lead is None and hasattr(item.product.producer, "profile"):
                lead = getattr(item.product.producer.profile, "lead_time", 2)

            if lead and lead > max_lead:
                max_lead = lead

        except Exception:
            pass

    days_ahead = delivery_weekday - order_date.weekday()

    if days_ahead <= 0:
        days_ahead += 7

    candidate = order_date + timedelta(days=days_ahead)
    min_delivery = order_date + timedelta(days=max_lead)

    if candidate < min_delivery:
        candidate += timedelta(weeks=1)

    return candidate


@login_required
def recurring_order_setup(request):
    messages.info(
        request,
        "Add items to your cart and select 'Make this a recurring order' at checkout.",
    )
    return redirect("cart:detail")


@login_required
def recurring_order_list(request):
    recurring_orders = (
        RecurringOrder.objects
        .filter(customer=request.user)
        .prefetch_related("items", "items__product", "instances")
        .order_by("-created_at")
    )

    for recurring_order in recurring_orders:
        recurring_order.next_instance = recurring_order.instances.filter(
            status__in=[
                RecurringOrderInstance.Status.SCHEDULED,
                RecurringOrderInstance.Status.MODIFIED,
            ],
        ).first()

    unread_count = RecurringNotification.objects.filter(
        recipient=request.user,
        is_read=False,
    ).count()

    return render(request, "marketplace/recurring_order_list.html", {
        "recurring_orders": recurring_orders,
        "unread_count": unread_count,
    })


@login_required
def producer_refund_response(request, refund_id):
    refund = get_object_or_404(RefundRequest, pk=refund_id)

    is_involved = refund.order.producer_orders.filter(
        producer=request.user,
    ).exists()

    if not is_involved:
        return HttpResponseForbidden("You are not involved in this order.")

    if refund.status not in [RefundRequest.Status.PENDING]:
        messages.warning(request, "This refund has already been processed.")
        return redirect("marketplace:producer_order_list")

    if request.method == "POST":
        producer_note = request.POST.get("producer_note", "").strip()

        if not producer_note:
            messages.error(request, "Please provide a response.")
        else:
            refund.producer_note = producer_note
            refund.status = RefundRequest.Status.PRODUCER_RESPONDED
            refund.save()

            from apps.message.models import Message, MessageThread
            from django.contrib.auth.models import User

            admins = User.objects.filter(profile__role=Profile.Role.ADMIN)

            if admins.exists():
                thread = MessageThread.objects.create(
                    subject=f"Producer Response — Refund #{refund.id} for Order #{refund.order.id}",
                    created_by=request.user,
                    related_order=refund.order,
                )
                thread.participants.add(request.user, *admins)

                Message.objects.create(
                    thread=thread,
                    sender=request.user,
                    body=(
                        f"Producer {request.user.username} has responded to "
                        f"Refund Request #{refund.id} for Order #{refund.order.id}.\n\n"
                        f"Producer note: {producer_note}\n\n"
                        f"Please review and make a final decision."
                    ),
                )

            messages.success(request, "Your response has been submitted.")
            return redirect("marketplace:producer_order_list")

    return render(request, "marketplace/producer_refund_response.html", {
        "refund": refund,
    })


@login_required
def admin_refund_decision(request, refund_id):
    if not hasattr(request.user, "profile") or request.user.profile.role != Profile.Role.ADMIN:
        return HttpResponseForbidden("Admin access required.")

    refund = get_object_or_404(RefundRequest, pk=refund_id)

    if refund.status in [RefundRequest.Status.APPROVED, RefundRequest.Status.REJECTED]:
        messages.warning(request, "This refund has already been resolved.")
        return redirect("accounts:admin_dashboard")

    if request.method == "POST":
        decision = request.POST.get("decision")
        admin_note = request.POST.get("admin_note", "").strip()

        if decision not in ["approve", "reject"]:
            messages.error(request, "Invalid decision.")
        else:
            refund.admin_note = admin_note
            refund.resolved_by = request.user
            refund.resolved_at = timezone.now()

            if decision == "approve":
                refund.status = RefundRequest.Status.APPROVED
                refund.order.status = Order.Status.CANCELLED
                refund.order.save()

                for producer_order in refund.order.producer_orders.all():
                    producer_order.status = ProducerOrder.Status.CANCELLED
                    producer_order.save()

                notification_body = (
                    f"Dear {refund.customer.username},\n\n"
                    f"Your refund request for Order #{refund.order.id} has been APPROVED.\n\n"
                    f"Refund Amount: £{refund.refund_amount}\n\n"
                    f"Admin note: {admin_note}\n\n"
                    f"The full amount will be returned to you. "
                    f"Thank you for your patience."
                )

            else:
                refund.status = RefundRequest.Status.REJECTED
                notification_body = (
                    f"Dear {refund.customer.username},\n\n"
                    f"Your refund request for Order #{refund.order.id} has been REJECTED.\n\n"
                    f"Admin note: {admin_note or 'N/A'}\n\n"
                    f"If you have further concerns please contact us."
                )

            refund.save()

            from apps.message.models import Message, MessageThread

            thread = MessageThread.objects.create(
                subject=f"Refund Request #{refund.id} — {refund.get_status_display()}",
                created_by=request.user,
                related_order=refund.order,
            )
            thread.participants.add(request.user, refund.customer)

            Message.objects.create(
                thread=thread,
                sender=request.user,
                body=notification_body,
            )

            messages.success(request, f"Refund {refund.get_status_display()} successfully.")
            return redirect("accounts:admin_dashboard")

    return render(request, "marketplace/admin_refund_decision.html", {
        "refund": refund,
    })


@login_required
def refund_list(request):
    if not hasattr(request.user, "profile") or request.user.profile.role != Profile.Role.ADMIN:
        return HttpResponseForbidden("Admin access required.")

    refunds = (
        RefundRequest.objects
        .select_related("order", "customer", "resolved_by")
        .all()
    )

    status_filter = request.GET.get("status", "")

    if status_filter:
        refunds = refunds.filter(status=status_filter)

    return render(request, "marketplace/refund_list.html", {
        "refunds": refunds,
        "status_filter": status_filter,
        "statuses": RefundRequest.Status.choices,
    })


def recurring_order_detail(request, pk):
    recurring_order = get_object_or_404(RecurringOrder, pk=pk, customer=request.user)

    instances = recurring_order.instances.exclude(
        status=RecurringOrderInstance.Status.PROCESSED,
    ).order_by("scheduled_date")

    notifications = RecurringNotification.objects.filter(
        recurring_order=recurring_order,
        recipient=request.user,
    ).order_by("-created_at")[:10]

    return render(request, "marketplace/recurring_order_detail.html", {
        "recurring_order": recurring_order,
        "instances": instances,
        "notifications": notifications,
    })


@login_required
def recurring_order_pause(request, pk):
    recurring_order = get_object_or_404(RecurringOrder, pk=pk, customer=request.user)

    if recurring_order.status == RecurringOrder.Status.ACTIVE:
        recurring_order.status = RecurringOrder.Status.PAUSED
        recurring_order.save()
        messages.success(request, "Recurring order paused.")

    elif recurring_order.status == RecurringOrder.Status.PAUSED:
        recurring_order.status = RecurringOrder.Status.ACTIVE
        recurring_order.next_order_date = recurring_order.calculate_next_order_date()
        recurring_order.save()
        _generate_next_instance(recurring_order)
        messages.success(request, "Recurring order resumed.")

    return redirect("marketplace:recurring_order_list")


@login_required
def recurring_order_cancel(request, pk):
    recurring_order = get_object_or_404(RecurringOrder, pk=pk, customer=request.user)

    if request.method == "POST":
        recurring_order.status = RecurringOrder.Status.CANCELLED
        recurring_order.save()
        messages.success(request, "Recurring order cancelled.")
        return redirect("marketplace:recurring_order_list")

    return render(request, "marketplace/recurring_order_cancel.html", {
        "recurring_order": recurring_order,
    })


@login_required
def recurring_order_modify_instance(request, instance_pk):
    instance = get_object_or_404(
        RecurringOrderInstance,
        pk=instance_pk,
        recurring_order__customer=request.user,
        status__in=[
            RecurringOrderInstance.Status.SCHEDULED,
            RecurringOrderInstance.Status.MODIFIED,
        ],
    )

    products = (
        Product.objects.filter(is_active=True, stock_quantity__gt=0)
        .select_related("category", "producer")
        .order_by("category__name", "name")
    )

    if instance.items.exists():
        current_items = {
            item.product_id: item.quantity
            for item in instance.items.all()
        }
    else:
        current_items = {
            item.product_id: item.quantity
            for item in instance.recurring_order.items.all()
        }

    if request.method == "POST":
        instance.items.all().delete()

        for key, value in request.POST.items():
            if key.startswith("qty_") and value.strip() and int(value) > 0:
                product_id = key.replace("qty_", "")

                try:
                    product = Product.objects.get(id=product_id, is_active=True)
                    RecurringOrderInstanceItem.objects.create(
                        instance=instance,
                        product=product,
                        quantity=int(value),
                    )
                except Product.DoesNotExist:
                    pass

        instance.status = RecurringOrderInstance.Status.MODIFIED
        instance.save()
        messages.success(request, "This week's order updated. Template unchanged.")
        return redirect("marketplace:recurring_order_detail", pk=instance.recurring_order.pk)

    return render(request, "marketplace/recurring_order_modify_instance.html", {
        "instance": instance,
        "products": products,
        "current_items": current_items,
    })


@login_required
def recurring_order_skip_instance(request, instance_pk):
    instance = get_object_or_404(
        RecurringOrderInstance,
        pk=instance_pk,
        recurring_order__customer=request.user,
    )

    instance.status = RecurringOrderInstance.Status.SKIPPED
    instance.save()
    messages.success(request, "This week's order skipped.")
    return redirect("marketplace:recurring_order_detail", pk=instance.recurring_order.pk)


@login_required
def recurring_notifications(request):
    notifications = (
        RecurringNotification.objects
        .filter(recipient=request.user)
        .select_related("recurring_order")
        .order_by("-created_at")
    )

    notifications.filter(is_read=False).update(is_read=True)

    return render(request, "marketplace/recurring_notifications.html", {
        "notifications": notifications,
    })


def recurring_notifications_count(request):
    if not request.user.is_authenticated:
        return JsonResponse({"count": 0})

    count = RecurringNotification.objects.filter(
        recipient=request.user,
        is_read=False,
    ).count()

    return JsonResponse({"count": count})


def _generate_next_instance(recurring_order):
    if not recurring_order.next_order_date:
        return

    order_date = recurring_order.next_order_date
    template_items = list(
        recurring_order.items.select_related("product", "product__producer")
    )

    delivery_date = _delivery_date_respecting_lead_time(
        order_date,
        recurring_order.delivery_day,
        template_items,
    )

    existing = RecurringOrderInstance.objects.filter(
        recurring_order=recurring_order,
        scheduled_date=order_date,
    ).exists()

    if not existing:
        instance = RecurringOrderInstance.objects.create(
            recurring_order=recurring_order,
            scheduled_date=order_date,
            delivery_date=delivery_date,
        )

        for template_item in template_items:
            RecurringOrderInstanceItem.objects.create(
                instance=instance,
                product=template_item.product,
                quantity=template_item.quantity,
            )

        days_until = (order_date - date.today()).days

        if days_until <= 2:
            order_label = recurring_order.name or f"Recurring order #{recurring_order.id}"

            _notify(
                recipient=recurring_order.customer,
                notification_type=RecurringNotification.Type.ORDER_UPCOMING,
                message=(
                    f"Your recurring order '{order_label}' will be processed on "
                    f"{order_date.strftime('%A, %d %b %Y')}. "
                    f"Delivery expected: {delivery_date.strftime('%A, %d %b %Y')}. "
                    f"You can still modify or skip this delivery."
                ),
                recurring_order=recurring_order,
            )


def process_recurring_orders():
    today = date.today()

    due_instances = (
        RecurringOrderInstance.objects
        .filter(
            scheduled_date=today,
            status__in=[
                RecurringOrderInstance.Status.SCHEDULED,
                RecurringOrderInstance.Status.MODIFIED,
            ],
            recurring_order__status=RecurringOrder.Status.ACTIVE,
        )
        .select_related("recurring_order", "recurring_order__customer")
    )

    for instance in due_instances:
        _process_instance(instance)


def _process_instance(instance):
    recurring_order = instance.recurring_order
    customer = recurring_order.customer

    if instance.items.exists():
        items = list(instance.items.select_related("product", "product__producer"))
    else:
        items = [
            type("Item", (), {"product": item.product, "quantity": item.quantity})()
            for item in recurring_order.items.select_related("product", "product__producer")
        ]

    if not items:
        return

    unavailable = []

    for item in items:
        if not item.product.is_active or item.product.stock_quantity < item.quantity:
            unavailable.append(item.product.name)

            _notify(
                recipient=customer,
                notification_type=RecurringNotification.Type.PRODUCT_UNAVAILABLE,
                message=(
                    f"'{item.product.name}' in your recurring order is currently unavailable "
                    f"or has insufficient stock. It will be excluded from this delivery."
                ),
                recurring_order=recurring_order,
            )

        else:
            unit_price = item.product.price
            template_item = recurring_order.items.filter(product=item.product).first()

            if template_item and template_item.unit_price and unit_price != template_item.unit_price:
                _notify(
                    recipient=customer,
                    notification_type=RecurringNotification.Type.ORDER_UPCOMING,
                    message=(
                        f"Price change for '{item.product.name}' in your recurring order: "
                        f"was £{template_item.unit_price}, now £{unit_price}. "
                        f"This delivery will be charged at the new price."
                    ),
                    recurring_order=recurring_order,
                )

    producers = defaultdict(list)
    subtotal = Decimal("0.00")

    for item in items:
        if item.product.name in unavailable:
            continue

        unit_price = item.product.price
        line_total = unit_price * item.quantity
        subtotal += line_total

        producers[item.product.producer.username].append({
            "name": item.product.name,
            "price": float(unit_price),
            "qty": item.quantity,
            "total": float(line_total),
            "id": item.product.id,
        })

    if not producers:
        instance.status = RecurringOrderInstance.Status.SKIPPED
        instance.save()
        return

    commission = (subtotal * COMMISSION_RATE).quantize(TWO_PLACES)
    total = (subtotal + commission).quantize(TWO_PLACES)

    success, pay_ref, pay_error = _simulate_charge(recurring_order, total)

    if not success:
        instance.payment_status = RecurringOrderInstance.PaymentStatus.FAILED
        instance.save()

        _notify(
            recipient=customer,
            notification_type=RecurringNotification.Type.PAYMENT_FAILED,
            message=(
                f"Payment failed for your recurring order "
                f"'{recurring_order.name or f'#{recurring_order.id}'}': {pay_error} "
                f"Please update your card details to resume automatic payments."
            ),
            recurring_order=recurring_order,
        )
        return

    order_number = "REC-" + str(random.randint(10000, 99999))
    User = get_user_model()

    db_order = Order.objects.create(
        customer=customer,
        delivery_address=recurring_order.delivery_address,
        delivery_postcode=recurring_order.delivery_postcode,
        special_instructions="Auto-generated recurring order",
        total_amount=total,
    )

    for producer_username, order_items in producers.items():
        producer = User.objects.get(username=producer_username)

        producer_order = ProducerOrder.objects.create(
            order=db_order,
            producer=producer,
            delivery_date=instance.delivery_date,
            status=ProducerOrder.Status.PENDING,
            total_value=Decimal("0.00"),
        )

        total_value = Decimal("0.00")

        for item in order_items:
            product = Product.objects.get(id=item["id"])

            OrderItem.objects.create(
                producer_order=producer_order,
                product=product,
                quantity=item["qty"],
                unit_price=Decimal(str(item["price"])),
            )

            total_value += Decimal(str(item["price"])) * item["qty"]
            product.stock_quantity = max(0, product.stock_quantity - item["qty"])
            product.save()

        producer_order.total_value = total_value.quantize(TWO_PLACES)
        producer_order.save()

        _notify(
            recipient=producer,
            notification_type=RecurringNotification.Type.PRODUCER_NOTICE,
            message=(
                f"Recurring order {order_number} has been placed. "
                f"Delivery required by {instance.delivery_date.strftime('%A, %d %b %Y')}. "
                f"Items: {', '.join(str(item['name']) + ' x' + str(item['qty']) for item in order_items)}."
            ),
            recurring_order=recurring_order,
        )

    order_data = {
        "order_number": order_number,
        "address": recurring_order.delivery_address,
        "order_date": timezone.now().strftime("%Y-%m-%d"),
        "delivery_date": str(instance.delivery_date),
        "payment": recurring_order.payment_method,
        "subtotal": float(subtotal),
        "commission": float(commission),
        "total": float(total),
        "producers": dict(producers),
        "recurring": True,
        "unavailable_items": unavailable,
        "payment_reference": pay_ref,
    }

    CustomerOrderHistory.objects.create(
        customer=customer,
        order_number=order_number,
        order_data=order_data,
    )

    instance.status = RecurringOrderInstance.Status.PROCESSED
    instance.order_number = order_number
    instance.payment_status = RecurringOrderInstance.PaymentStatus.PAID
    instance.payment_amount = total
    instance.payment_reference = pay_ref
    instance.save()

    _notify(
        recipient=customer,
        notification_type=RecurringNotification.Type.ORDER_PROCESSED,
        message=(
            f"Your recurring order has been processed. Order {order_number}, "
            f"total £{total}, charged to card ending {recurring_order.card_last_four}. "
            f"Delivery expected: {instance.delivery_date.strftime('%A, %d %b %Y')}."
        ),
        recurring_order=recurring_order,
    )

    recurring_order.last_generated = date.today()
    recurring_order.next_order_date = recurring_order.calculate_next_order_date()
    recurring_order.save()
    _generate_next_instance(recurring_order)