from collections import defaultdict
from decimal import Decimal
import random

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Profile
from .forms import CheckoutForm, ProductForm
from .models import Category, Product


# ----------------------------
# HELPERS
# ----------------------------

def _require_producer(request):
    if not request.user.is_authenticated:
        return False
    profile, _ = Profile.objects.get_or_create(user=request.user)
    return profile.role == "PRODUCER"


# ----------------------------
# PRODUCT LIST
# ----------------------------

def product_list(request):
    products = Product.objects.all().select_related("category", "producer").prefetch_related("allergens")
    categories = Category.objects.order_by("name")

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
            Q(other_allergen_info__icontains=query)
        ).distinct()

    if allergen_filter == "with":
        products = products.filter(
            Q(allergens__isnull=False) | ~Q(other_allergen_info="")
        ).distinct()
    elif allergen_filter == "without":
        products = products.filter(
            allergens__isnull=True,
            other_allergen_info=""
        ).distinct()

    context = {
        "products": products,
        "categories": categories,
        "selected_category": selected_category,
        "selected_season": selected_season,
        "seasons": Product.SEASON_CHOICES,
        "query": query,
        "allergen_filter": allergen_filter,
    }
    return render(request, "marketplace/product_list.html", context)


# ----------------------------
# PRODUCT DETAIL
# ----------------------------

def product_detail(request, pk):
    product = get_object_or_404(
        Product.objects.select_related("category", "producer").prefetch_related("allergens"),
        pk=pk
    )
    return render(request, "marketplace/product_detail.html", {"product": product})


# ----------------------------
# PRODUCER PRODUCT LIST
# ----------------------------

@login_required
def producer_product_list(request):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    products = Product.objects.filter(producer=request.user).select_related("category").order_by("-created_at")
    return render(request, "producer/product_list.html", {"products": products})


# ----------------------------
# PRODUCT CREATE
# ----------------------------

@login_required
def product_create(request):
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    if request.method == "POST":
        form = ProductForm(request.POST)
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
        return HttpResponseForbidden("Producer access only.")

    product = get_object_or_404(Product, pk=pk, producer=request.user)

    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
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
        return HttpResponseForbidden("Producer access only.")

    product = get_object_or_404(Product, pk=pk, producer=request.user)

    if request.method == "POST":
        product.delete()
        messages.success(request, "Product deleted.")
        return redirect("marketplace:producer_product_list")

    return render(
        request,
        "producer/product_confirm_delete.html",
        {"product": product},
    )


# ----------------------------
# CHECKOUT (MULTI PRODUCER)
# ----------------------------

def checkout(request):
    cart = request.session.get("cart", {})
    producers = defaultdict(list)
    subtotal = Decimal("0.00")

    for product_id, qty in cart.items():
        try:
            product = Product.objects.select_related("producer").get(id=int(product_id))
        except Product.DoesNotExist:
            continue

        qty = int(qty)
        line_total = product.price * qty
        subtotal += line_total

        lead_time = getattr(product.producer, "lead_time", 2)

        producers[product.producer.username].append({
            "name": product.name,
            "price": float(product.price),
            "qty": qty,
            "total": float(line_total),
            "lead_time": lead_time,
        })

    commission = subtotal * Decimal("0.05")
    total = subtotal + commission

    if request.method == "POST":
        form = CheckoutForm(request.POST)
        if form.is_valid():
            delivery_address = form.cleaned_data["delivery_address"]
            delivery_date = form.cleaned_data["delivery_date"]
            payment_method = form.cleaned_data["payment_method"]

            request.session["order"] = {
                "address": delivery_address,
                "date": str(delivery_date),
                "payment": payment_method,
                "subtotal": float(subtotal),
                "commission": float(commission),
                "total": float(total),
                "producers": dict(producers),
            }

            return redirect("marketplace:payment")
    else:
        initial = {}
        if request.user.is_authenticated:
            initial["delivery_address"] = request.user.email or request.user.username

        form = CheckoutForm(initial=initial)

    return render(request, "checkout.html", {
        "form": form,
        "producers": dict(producers),
        "subtotal": subtotal,
        "commission": commission,
        "total": total,
    })


# ----------------------------
# PAYMENT
# ----------------------------

def payment(request):
    order = request.session.get("order")

    if not order:
        return redirect("marketplace:product_list")

    if request.method == "POST":
        order_number = "ORD-" + str(random.randint(10000, 99999))
        print("NEW ORDER RECEIVED")
        print("Order Number:", order_number)

        for producer, items in order["producers"].items():
            print(f"\nNotification for producer: {producer}")
            for item in items:
                print(f"- {item['name']} x{item['qty']} (£{item['total']})")

        if "order" in request.session:
            del request.session["order"]

        return render(request, "confirmation.html", {
            "order_number": order_number,
            "address": order["address"],
            "date": order["date"],
            "payment": order["payment"],
            "subtotal": order["subtotal"],
            "commission": order["commission"],
            "total": order["total"],
            "producers": order["producers"],
        })

    return render(request, "payment.html", {
        "order": order,
    })