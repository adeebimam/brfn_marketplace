<<<<<<< HEAD
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import Q
=======
from django.shortcuts import render, redirect
from decimal import Decimal
from .forms import CheckoutForm
from .models import Product
import random
from collections import defaultdict
>>>>>>> melee


<<<<<<< HEAD

# Create your views here.
=======
# ----------------------------
# PRODUCT LIST
# ----------------------------
>>>>>>> melee

def product_list(request):
    products = Product.objects.all()
    return render(request, "marketplace/product_list.html", {"products": products})

<<<<<<< HEAD
    selected_category = request.GET.get("category")
    selected_season = request.GET.get("season")
    selected_category = request.GET.get("category", "").strip()
    query = request.GET.get("q", "").strip()
    allergen_filter = request.GET.get("allergen_filter", "").strip()

    if selected_category:
        products = products.filter(category_id=selected_category)
    if selected_season:
        products = products.filter(season = selected_season)

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
        "selected_seasons":selected_season,
        "seasons": Product.SEASON_CHOICES
        "query": query,
        "allergen_filter": allergen_filter,
    }
    return render(request, "marketplace/product_list.html", context)
=======
>>>>>>> melee


def product_detail(request, pk):
    return render(request, "marketplace/product_detail.html")


def producer_product_list(request):
    return render(request, "producer/product_list.html")


def product_create(request):
<<<<<<< HEAD
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

    return render(request, "marketplace/product_form.html", {"form": form, "mode": "create"})
=======
    return render(request, "producer/product_form.html")
>>>>>>> melee


def product_update(request, pk):
<<<<<<< HEAD
    if not _require_producer(request):
        return HttpResponseForbidden("Producer access only.")

    product = get_object_or_404(Product, pk=pk, producer=request.user)

    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            product = form.save(commit=False)
            product.save()
            form.save_m2m()
            messages.success(request, "Product updated.")
            return redirect("marketplace:producer_product_list")
    else:
        form = ProductForm(instance=product)

    return render(request, "marketplace/product_form.html", {"form": form, "mode": "edit"})
=======
    return render(request, "producer/product_form.html")
>>>>>>> melee


def product_delete(request, pk):
    return render(request, "producer/product_confirm_delete.html")


# ----------------------------
# CHECKOUT (MULTI PRODUCER)
# ----------------------------

def checkout(request):

    cart = request.session.get("cart", {})
    producers = defaultdict(list)
    subtotal = Decimal("0.00")

    # build producer groups
    for product_id, qty in cart.items():

        product = Product.objects.get(id=int(product_id))

        line_total = product.price * qty
        subtotal += line_total

        
        producers[product.producer.username].append({
            "name": product.name,
            "price": float(product.price),
            "qty": qty,
            "total": float(line_total),
            "lead_time": getattr(product.producer, "lead_time", 2)
        })

    commission = subtotal * Decimal("0.05")
    total = subtotal + commission


    # POST = continue to payment
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
                "producers": dict(producers)
            }

            return redirect("marketplace:payment")

    else:

        initial = {}

        if request.user.is_authenticated:
            initial["delivery_address"] = request.user.email or request.user.username

        form = CheckoutForm(initial=initial)


    return render(request, "checkout.html", {
        "form": form,
        "producers": dict(producers),   # important for template
        "subtotal": subtotal,
        "commission": commission,
        "total": total
    })


# ----------------------------
# PAYMENT
# ----------------------------

def payment(request):

    order = request.session.get("order")

    if not order:
        return redirect("marketplace:product_list")

    if request.method == "POST":

<<<<<<< HEAD
    return render(request, "marketplace/product_confirm_delete.html", {"product": product})
=======
        order_number = "ORD-" + str(random.randint(10000, 99999))
        print("NEW ORDER RECEIVED")
        print("Order Number:", order_number)

        for producer, items in order["producers"].items():

            print(f"\nNotification for producer: {producer}")

            for item in items:
                print(f"- {item['name']} x{item['qty']} (£{item['total']})")





        
       
        
        return render(request, "confirmation.html", {
            "order_number": order_number,
            "address": order["address"],
            "date": order["date"],
            "payment": order["payment"],
            "subtotal": order["subtotal"],
            "commission": order["commission"],
            "total": order["total"],
            "producers": order["producers"]
        })

    return render(request, "payment.html", {
        "order": order
    })
>>>>>>> melee
