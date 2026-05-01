import csv
import random
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from tracemalloc import start
from urllib import request
from .forms import CheckoutForm, ProductForm, ProducerOrderStatusForm, ReviewForm


from .services import update_producer_order_status

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from datetime import date, timedelta, datetime

from apps.cart.models import Cart, CartItem

from django.http import Http404


from apps.accounts.models import Profile

from .models import (

    Allergen, Category, CustomerOrderHistory, Product, MONTH_NAMES,
    ProducerOrder, ProducerOrderStatusHistory, Review,

    Allergen, Category, Product, MONTH_NAMES,Order,
    ProducerOrder, ProducerOrderStatusHistory,

)


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
    # Only show products where stock > 0 AND is_active (not marked "Not available")
    products = (
        Product.objects.filter(is_active=True, stock_quantity__gt=0)
        .select_related("category", "producer")
        .prefetch_related("allergens")
    )
    categories = Category.objects.order_by("name")
    allergens = Allergen.objects.order_by("name")

    selected_category = request.GET.get("category", "").strip()
    selected_season = request.GET.get("season", "").strip()
    query = request.GET.get("q", "").strip()
    allergen_filter = request.GET.get("allergen_filter", "").strip()

    if selected_category:
        products = products.filter(category_id=selected_category)

    if selected_season:
        products = products.filter(season=selected_season)

    if query:
        products = products.filter(
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(allergens__name__icontains=query) |
            Q(other_allergen_info__icontains=query) |
            Q(producer__username__icontains=query)
        ).distinct()

    if allergen_filter == "with":
        products = products.filter(
            Q(allergens__isnull=False) | ~Q(other_allergen_info="")
        ).distinct()
    elif allergen_filter == "without":
        products = products.exclude(
            allergens__isnull=False
        ).filter(other_allergen_info="")
    elif allergen_filter.startswith("specific_"):
        allergen_id = allergen_filter.split("_")[1]
        products = products.filter(allergens__id=allergen_id)

    # Auto-hide out-of-season products (date-based filtering)
    today = date.today()
    current_month = today.month
    # Keep products that are year-round (ALL or no months set) OR currently in season
    products = [p for p in products if p.is_in_season(today)]

    # Annotate each product with season status for template use
    for p in products:
        p.in_season_now = p.is_in_season(today)

    # Build allergen choices with pre-formatted value strings for template
    allergen_choices = [
        {"value": f"specific_{a.id}", "name": a.name}
        for a in allergens
    ]

    context = {
        "products": products,
        "categories": categories,
        "allergens": allergens,
        "allergen_choices": allergen_choices,
        "selected_category": selected_category,
        "selected_season": selected_season,
        "seasons": Product.SEASON_CHOICES,
        "query": query,
        "allergen_filter": allergen_filter,
        "current_month": current_month,
    }
    return render(request, "marketplace/product_list.html", context)


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

    return render(request, "marketplace/product_detail.html", {
        "product": product,
        "is_in_season": product.is_in_season(),
        "reviews": reviews,
        "average_rating": average_rating,
    })


@login_required
def create_review(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    order_history = [
    record.order_data
    for record in CustomerOrderHistory.objects.filter(customer=request.user)
]

    has_purchased = False

    for order in order_history:
        for producer, items in order.get("producers", {}).items():
            for item in items:
                if str(item.get("id")) == str(product_id):
                    has_purchased = True
                    break

    if not has_purchased:
        messages.error(request, "You can only review products you have purchased.")
        return redirect("marketplace:product_detail", pk=product_id)

    existing_review = Review.objects.filter(
        product=product,
        customer=request.user
    ).first()

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

            messages.success(request, "Review submitted successfully.")
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
    return render(request, "marketplace/producer_product_list.html", {"products": products})


# ----------------------------
# PRODUCT CREATE
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

    return render(
        request,
        "marketplace/product_form.html",
        {"form": form, "mode": "create"},
    )


# ----------------------------
# PRODUCT UPDATE
# ----------------------------

@login_required
def product_update(request, pk):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    product = get_object_or_404(
        Product,
        pk=pk,
        producer=request.user,
    )

    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=product)

        if form.is_valid():
            product = form.save(commit=False)
            product.producer = request.user
            product.save()
            form.save_m2m()

            messages.success(request, "Product updated.")
            return redirect("marketplace:producer_product_list")
    else:
        form = ProductForm(instance=product)

    return render(
        request,
        "marketplace/product_form.html",
        {"form": form, "mode": "edit"},
    )


# ----------------------------
# PRODUCT DELETE
# ----------------------------

@login_required
def product_delete(request, pk):
    if not _require_producer(request):
        return _producer_access_denied_response(request)

    product = get_object_or_404(
        Product,
        pk=pk,
        producer=request.user,
    )

    if request.method == "POST":
        product.delete()

        messages.success(request, "Product deleted.")
        return redirect("marketplace:producer_product_list")

    return render(
        request,
        "marketplace/product_confirm_delete.html",
        {"product": product},
    )


# ----------------------------
# CHECKOUT (MULTI PRODUCER)
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

        if form.is_valid():
            delivery_address = form.cleaned_data["delivery_address"]
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

        print("NEW ORDER RECEIVED")
        print("Order Number:", order_number)
        print("Session order data:", order)
        try:
            from .models import Order, ProducerOrder, OrderItem, Product
            from django.contrib.auth import get_user_model
            User = get_user_model()
            customer = request.user
            if not customer.is_authenticated:
                raise Exception("User is not authenticated!")
            # Create main order
            db_order = Order.objects.create(
                customer=customer,
                delivery_address=order["address"],
                delivery_postcode="",  # You may want to collect this in your form
                special_instructions="",  # Extend as needed
            )
            debug_info.append(f"Created Order: {db_order}")
            # For each producer, create a ProducerOrder
            for producer_username, items in order["producers"].items():
                debug_info.append(f"Processing producer: {producer_username}")
                producer = User.objects.get(username=producer_username)
                delivery_date = order.get("date") or None
                producer_order = ProducerOrder.objects.create(
                    order=db_order,
                    producer=producer,
                    delivery_date=delivery_date,
                    status=ProducerOrder.Status.PENDING,
                    total_value=0,
                )
                debug_info.append(f"Created ProducerOrder: {producer_order}")
                total_value = 0
                for item in items:
                    debug_info.append(f"Processing item: {item}")
                    product = Product.objects.get(name=item["name"], producer=producer)
                    debug_info.append(f"Found product: {product}")
                    OrderItem.objects.create(
                        producer_order=producer_order,
                        product=product,
                        quantity=item["qty"],
                        unit_price=item["price"],
                    )
                    total_value += item["qty"] * float(item["price"])
                    # Optionally, reduce stock
                    product.stock_quantity = max(0, product.stock_quantity - item["qty"])
                    product.save()
                    debug_info.append(f"Updated stock for {product.name}: {product.stock_quantity}")
                producer_order.total_value = total_value
                producer_order.save()
                debug_info.append(f"Saved ProducerOrder with total_value: {total_value}")
            print("DEBUG INFO:", debug_info)
        except Exception as e:
            import traceback
            error_message = f"Order creation failed: {e}"
            print(error_message)
            print(traceback.format_exc())
            return render(request, "payment.html", {"order": order, "error_message": error_message, "debug_info": debug_info})

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

        if "order" in request.session:
            del request.session["order"]

        if "cart" in request.session:
            del request.session["cart"]

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

    return render(request, "orders/payment.html", {
        "order": order,
    })


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
    orders = sorted(
    orders,
    key=lambda x: x.get("order_date") or x.get("date"),
    reverse=(sort == "newest")
    )
    if sort == "oldest":
        orders = sorted(orders, key=lambda x: x["order_date"])
    else:
        orders = sorted(orders, key=lambda x: x["order_date"], reverse=True)

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

    return render(
        request,
        "marketplace/producer_order_detail.html",
        {"po": po},
    )


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
        .filter(
            producer=producer,
            status=ProducerOrder.Status.DELIVERED
        )
        .select_related("order", "order__customer")
        .prefetch_related("items", "items__product")
        .order_by("-delivery_date", "-id")
    )

    weekly_orders = all_delivered_orders.filter(
        delivery_date__range=(week_start, week_end)
    ).order_by("-delivery_date", "-id")

    gross_total = sum(
        (order.total_value for order in weekly_orders),
        Decimal("0.00")
    ).quantize(TWO_PLACES)

    commission, net_payment = _compute_financials(gross_total)

    tax_year_orders = all_delivered_orders.filter(
        delivery_date__gte=tax_year_start
    )

    tax_year_total = sum(
        (order.total_value for order in tax_year_orders),
        Decimal("0.00")
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
            (o.total_value for o in orders_in_week),
            Decimal("0.00")
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

    context = {
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
    }

    return render(
        request,
        "marketplace/producer_payments.html",
        context
    )


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

        gross = order.total_value.quantize(TWO_PLACES)
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
        .filter(
            producer=request.user,
            status=ProducerOrder.Status.DELIVERED,
        )
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
        status_choices = [(status, ProducerOrder.Status(status).label) for status in next_statuses]
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
        status_choices = [(status, ProducerOrder.Status(status).label) for status in next_statuses]
        form = ProducerOrderStatusForm(initial={"status": po.status}, status_choices=status_choices)

    return render(
        request,
        "marketplace/producer_order_update_status.html",
        {
            "po": po,
            "form": form,
        },
    )
# -----------------------------
# TC21 - Order History
# -----------------------------

@login_required
def order_history(request):
    orders = [
    record.order_data
    for record in CustomerOrderHistory.objects.filter(customer=request.user)
]

    # newest first
    #orders = list(reversed(orders))

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

    if start_date and end_date:
        if end_date < start_date:
            messages.error(request, "End date cannot be before start date.")
        else:
            filtered_orders = []
            for o in orders:
                raw_order_date = o.get("order_date") or o.get("date")
                if not raw_order_date:
                    continue

                try:
                    order_date = datetime.strptime(raw_order_date, "%Y-%m-%d").date()
                except ValueError:
                    continue

                if start_date <= order_date <= end_date:
                    filtered_orders.append(o)

            orders = filtered_orders

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

    return render(request, "orders/order_detail.html", {
        "order": order
    })


@login_required
def reorder(request, order_id):
    from decimal import Decimal
    from apps.cart.models import Cart, CartItem
    from apps.marketplace.models import Product

    # ✅ FIX: get orders from DATABASE (not session)
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
    suggested_items = []

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

            # ✅ PRICE CHANGE DETECT
            if old_price != new_price:
                price_changed_items.append(
                    f"{product.name}: was £{old_price}, now £{new_price}"
                )

            cart_item, created = CartItem.objects.get_or_create(
                cart=cart,
                product=product
            )

            qty = int(item.get("qty", 1))

            if created:
                cart_item.quantity = qty
            else:
                cart_item.quantity += qty

            cart_item.save()

    # ✅ SHOW POPUPS (this is what you want)

    if price_changed_items:
        messages.warning(
            request,
            "⚠ Price changes detected:\n" + "\n".join(price_changed_items)
        )

    if unavailable_items:
        messages.error(
            request,
            "❌ Some items unavailable: " + ", ".join(unavailable_items)
        )

    messages.success(request, "✅ Items added to cart with latest prices.")

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