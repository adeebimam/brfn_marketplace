from django.shortcuts import render, redirect
from decimal import Decimal
from .forms import CheckoutForm
from .models import Product
import random
from collections import defaultdict


# ----------------------------
# PRODUCT LIST
# ----------------------------

def product_list(request):
    products = Product.objects.all()
    return render(request, "marketplace/product_list.html", {"products": products})


def product_detail(request, pk):
    return render(request, "marketplace/product_detail.html")


def producer_product_list(request):
    return render(request, "producer/product_list.html")


def product_create(request):
    return render(request, "producer/product_form.html")


def product_update(request, pk):
    return render(request, "producer/product_form.html")


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