import csv
import random
from collections import defaultdict
from datetime import date, timedelta, datetime
from decimal import Decimal
from decimal import Decimal, ROUND_HALF_UP
from tracemalloc import start
from urllib import request
from .forms import CheckoutForm, ProductForm, ProducerOrderStatusForm, PurchaseReviewForm, ReviewForm
from .models import PurchaseReview
from .models import CustomerOrderHistory
from .services import update_producer_order_status

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.cart.models import Cart, CartItem
from django.http import Http404
from apps.accounts.models import Profile

from .models import (
    Allergen, Category, CustomerOrderHistory, Product, MONTH_NAMES,
    Order, ProducerOrder, ProducerOrderStatusHistory, Review,
)

TWO_PLACES = Decimal("0.01")
COMMISSION_RATE = Decimal("0.05")


# -----------------------------
# Helper functions
# -----------------------------

def _compute_financials(gross):
    gross = gross.quantize(TWO_PLACES)
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
    days_since_monday = today.weekday()
    last_monday = today - timedelta(days=days_since_monday + 7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday, last_sunday


def _uk_tax_year_start(today):
    if today.month > 4 or (today.month == 4 and today.day >= 6):
        return date(today.year, 4, 6)
    return date(today.year - 1, 4, 6)


def _anonymise_customer(user):
    return f"{user.first_name} {user.last_name[0]}." if user.last_name else user.username


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

    for p in products:
        p.in_season_now = p.is_in_season(today)
        p.real_allergens = [a for a in p.allergens.all() if a.name != "No common allergens"]

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

        products = [
            p for p in products
            if p.food_miles is None or p.food_miles <= max_miles
        ]
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
    product = get_object_or_404(
        Product.objects.select_related("category", "producer").prefetch_related("allergens"),
        pk=pk,
        is_active=True,
    )

    if (not product.is_active or product.stock_quantity <= 0 or not product.is_in_season()):
        if request.user != product.producer:
            raise Http404("This product is not currently available.")

    reviews = Review.objects.filter(product=product)

    average_rating = None
    if reviews.exists():
        average_rating = round(sum(r.rating for r in reviews) / reviews.count(), 1)

    food_miles = None
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

    return render(request, "marketplace/product_detail.html", {
        "product": product,
        "is_in_season": product.is_in_season(),
        "reviews": reviews,
        "average_rating": average_rating,
        "food_miles": food_miles,
    })


@login_required
def create_review(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    # Check purchased product from database order history
    order_records = CustomerOrderHistory.objects.filter(customer=request.user)

    has_purchased = False

    for record in order_records:
        order = record.order_data

        for producer, items in order.get("producers", {}).items():
            for item in items:
                if str(item.get("id")) == str(product_id):
                    has_purchased = True
                    break

            if has_purchased:
                break

        if has_purchased:
            break

    if not has_purchased:
        messages.error(request, "You can only review products you have purchased.")
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

    return render(request, "marketplace/review_form.html", {
        "form": form,
        "product": product,
    })


# -----------------------------
# Producer product management
# -----------------------------

@login_required
def producer_product_list(request):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    products = Product.objects.filter(producer=request.user).select_related("category").order_by("-created_at")

    from .models import StockNotification
    active_alerts_count = StockNotification.objects.filter(
        producer=request.user,
        is_resolved=False
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

def checkout(request):
    if not request.user.is_authenticated:
        return redirect("accounts:login")

    cart, _ = Cart.objects.get_or_create(user=request.user)
    cart_items = CartItem.objects.select_related("product", "product__producer").filter(cart=cart)

    producers = defaultdict(list)
    subtotal = Decimal("0.00")

    for cart_item in cart_items:
        product = cart_item.product
        if not product.is_active or product.stock_quantity <= 0:
            continue
        qty = int(cart_item.quantity)
        line_total = product.price * qty
        subtotal += line_total
        lead_time = getattr(product.producer, "lead_time", 2)
        producers[product.producer.username].append({
            "name": product.name,
            "price": float(product.price),
            "qty": qty,
            "total": float(line_total),
            "lead_time": lead_time,
            "id": product.id,
        })

    commission = subtotal * Decimal("0.05")
    total = subtotal + commission

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
        initial = {}
        if request.user.is_authenticated:
            initial["delivery_address"] = request.user.email or request.user.username
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

def payment(request):
    order = request.session.get("order")
    if not order:
        return redirect("marketplace:product_list")

    error_message = None
    debug_info = []

    if request.method == "POST":
        card_number = request.POST.get("card_number", "").strip()
        expiry = request.POST.get("expiry", "").strip()
        cvc = request.POST.get("cvc", "").strip()

        if len(expiry) != 5 or expiry[2] != "/":
            messages.error(request, "Enter expiry date in MM/YY format.")
            return render(request, "orders/payment.html", {"order": order})

        month_part, year_part = expiry.split("/")
        if not (month_part.isdigit() and year_part.isdigit()):
            messages.error(request, "Enter expiry date in MM/YY format.")
            return render(request, "orders/payment.html", {"order": order})

        month = int(month_part)
        year = int(year_part)

        if month < 1 or month > 12:
            messages.error(request, "Enter a valid expiry month.")
            return render(request, "orders/payment.html", {"order": order})

        today = timezone.localdate()
        current_month = today.month
        current_year = today.year % 100

        if year < current_year or (year == current_year and month < current_month):
            messages.error(request, "Card expiry date cannot be in the past.")
            return render(request, "orders/payment.html", {"order": order})

        order_number = "ORD-" + str(random.randint(10000, 99999))

        try:
            from .models import Order, ProducerOrder, OrderItem, Product
            from django.contrib.auth import get_user_model
            User = get_user_model()
            customer = request.user
            if not customer.is_authenticated:
                raise Exception("User is not authenticated!")

            db_order = Order.objects.create(
                customer=customer,
                delivery_address=order["address"],
                delivery_postcode="",
                special_instructions="",
            )

            for producer_username, items in order["producers"].items():
                producer = User.objects.get(username=producer_username)
                delivery_date = order.get("date") or None
                producer_order = ProducerOrder.objects.create(
                    order=db_order,
                    producer=producer,
                    delivery_date=delivery_date,
                    status=ProducerOrder.Status.PENDING,
                    total_value=0,
                )
                total_value = 0
                for item in items:
                    product = Product.objects.get(name=item["name"], producer=producer)
                    OrderItem.objects.create(
                        producer_order=producer_order,
                        product=product,
                        quantity=item["qty"],
                        unit_price=item["price"],
                    )
                    total_value += item["qty"] * float(item["price"])
                    product.stock_quantity = max(0, product.stock_quantity - item["qty"])
                    product.save()

                producer_order.total_value = total_value
                debug_info.append(f"Updated stock for {product.name}: {product.stock_quantity}")
                producer_order.total_value = Decimal(str(total_value)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP
                )
                producer_order.save()

        except Exception as e:
            import traceback
            error_message = f"Order creation failed: {e}"
            print(error_message)
            print(traceback.format_exc())
            return render(request, "orders/payment.html", {
                "order": order,
                "error_message": error_message,
                "debug_info": debug_info
            })

        order_history = request.session.get("order_history", [])

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
            order_data=order_data
        )

        if request.user.is_authenticated:
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

    return render(request, "orders/payment.html", {"order": order})


# ----------------------------
# ALLERGEN TEST
# ----------------------------

def allergen_test(request):
    form = ProductForm()
    return render(request, "marketplace/allergen_test.html", {"form": form})


# -----------------------------
# TC-009 Producer Order List
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
    orders = list(orders)
    if sort == "oldest":
        orders = sorted(orders, key=lambda x: x.delivery_date or date.min)
    else:
        orders = sorted(orders, key=lambda x: x.delivery_date or date.min, reverse=True)

    return render(request, "marketplace/producer_order_list.html", {
        "orders": orders,
        "selected_status": status,
        "sort": sort,
        "status_choices": ProducerOrder.Status.choices,
    })


# -----------------------------
# TC-009 Order Detail Page
# -----------------------------

@login_required
def producer_order_detail(request, pk):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    po = get_object_or_404(
        ProducerOrder.objects
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product"),
        pk=pk,
        producer=request.user,
    )

    return render(request, "marketplace/producer_order_detail.html", {"po": po})


# -----------------------------
# TC-012 Producer Payment Settlement
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
        delivery_date__range=(week_start, week_end)
    ).order_by("-delivery_date", "-id")

    gross_total = sum(
        (order.total_value for order in weekly_orders), Decimal("0.00")
    ).quantize(TWO_PLACES)

    commission, net_payment = _compute_financials(gross_total)

    tax_year_orders = all_delivered_orders.filter(delivery_date__gte=tax_year_start)
    tax_year_total = sum(
        (order.total_value for order in tax_year_orders), Decimal("0.00")
    ).quantize(TWO_PLACES)

    settlement_reference = _build_settlement_ref(producer.id, week_start, week_end)

    history_map = defaultdict(list)
    for order in all_delivered_orders:
        order_week_start = order.delivery_date - timedelta(days=order.delivery_date.weekday())
        order_week_end = order_week_start + timedelta(days=6)
        history_map[(order_week_start, order_week_end)].append(order)

    historical_records = []
    for (hist_start, hist_end), orders_in_week in sorted(history_map.items(), reverse=True):
        if hist_start == week_start and hist_end == week_end:
            continue
        hist_gross = sum(
            (o.total_value for o in orders_in_week), Decimal("0.00")
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
            delivery_date__range=(week_start, week_end)
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
        "Settlement Reference", "Order Number", "Customer",
        "Delivery Date", "Items Sold", "Gross Amount",
        "Commission (5%)", "Net Payment", "Status",
    ])

    settlement_reference = _build_settlement_ref(producer.id, week_start, week_end)

    for order in orders:
        items_sold = ", ".join(
            f"{item.product.name} x{item.quantity}" for item in order.items.all()
        )
        gross = order.total_value.quantize(TWO_PLACES)
        commission, net = _compute_financials(gross)
        writer.writerow([
            settlement_reference, order.order.id,
            _anonymise_customer(order.order.customer),
            order.delivery_date, items_sold, gross, commission, net,
            order.get_status_display(),
        ])

    return response


@login_required
def producer_order_management(request):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

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
        ProducerOrder.Status.PENDING: [ProducerOrder.Status.CONFIRMED, ProducerOrder.Status.CANCELLED],
        ProducerOrder.Status.CONFIRMED: [ProducerOrder.Status.READY, ProducerOrder.Status.CANCELLED],
        ProducerOrder.Status.READY: [ProducerOrder.Status.DELIVERED],
        ProducerOrder.Status.DELIVERED: [],
        ProducerOrder.Status.CANCELLED: [],
    }

    if request.method == "POST":
        next_statuses = allowed_transitions.get(po.status, [])
        status_choices = [(s, ProducerOrder.Status(s).label) for s in next_statuses]
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
        next_statuses = allowed_transitions.get(po.status, [])
        status_choices = [(s, ProducerOrder.Status(s).label) for s in next_statuses]
        form = ProducerOrderStatusForm(initial={"status": po.status}, status_choices=status_choices)

    return render(request, "marketplace/producer_order_update_status.html", {"po": po, "form": form})


# -----------------------------
# TC21 - Order History
# -----------------------------

@login_required
def order_history(request):
    orders = [
        record.order_data
        for record in CustomerOrderHistory.objects.filter(customer=request.user)
    ]

    start = request.GET.get("start", "").strip()
    end = request.GET.get("end", "").strip()
    producer = request.GET.get("producer", "").strip()

    start_date = None
    end_date = None

    if start:
        try:
            start_date = datetime.strptime(start, "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, "Invalid start date.")

    if end:
        try:
            end_date = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, "Invalid end date.")

    if start_date and end_date and end_date < start_date:
        messages.error(request, "End date cannot be before start date.")
    elif start_date or end_date:
        filtered_orders = []
        for o in orders:
            raw_order_date = o.get("order_date") or o.get("date")
            if not raw_order_date:
                continue
            try:
                order_date = datetime.strptime(raw_order_date, "%Y-%m-%d").date()
            except ValueError:
                continue
            if start_date and order_date < start_date:
                continue
            if end_date and order_date > end_date:
                continue
            filtered_orders.append(o)
        orders = filtered_orders

    if producer:
        orders = [
            o for o in orders
            if producer.lower() in [p.lower() for p in o.get("producers", {}).keys()]
        ]

    return render(request, "orders/history.html", {
        "orders": orders,
        "start": start,
        "end": end,
        "producer": producer,
    })


@login_required
def order_detail(request, order_id):
    orders = [
        record.order_data
        for record in CustomerOrderHistory.objects.filter(customer=request.user)
    ]



    order = next((o for o in orders if str(o.get("order_number")) == str(order_id)), None)

    if not order:
        messages.error(request, "Order not found")
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
    orders = [
        record.order_data
        for record in CustomerOrderHistory.objects.filter(customer=request.user)
    ]
    order = next((o for o in orders if str(o.get("order_number")) == str(order_id)), None)

    if not order:
        messages.error(request, "Order not found")
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
        messages.warning(request, "Price changes detected:\n" + "\n".join(price_changed_items))

    if unavailable_items:
        messages.error(request, "Some items unavailable: " + ", ".join(unavailable_items))

    messages.success(request, "Items added to cart with latest prices.")
    return redirect("cart:detail")


@login_required
def download_receipt(request, order_id):
    orders = [
        record.order_data
        for record in CustomerOrderHistory.objects.filter(customer=request.user)
    ]
    order = next((o for o in orders if str(o.get("order_number")) == str(order_id)), None)

    if not order:
        messages.error(request, "Receipt not found")
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