from django.shortcuts import render, redirect

def checkout(request):
    return render(request, "orders/checkout.html")

def payment(request):
    total = request.session.get('order_total')
    cart_items = request.session.get('cart_items', [])
    # Retrieve checkout/session data
    address = request.session.get('delivery_address', '')
    date = request.session.get('delivery_date', '')
    payment_method = request.session.get('payment_method', '')
    producers = request.session.get('producers', {})
    subtotal = request.session.get('subtotal', total)
    commission = request.session.get('commission', '')
    if request.method == "POST":
        # Update session with latest POSTed values if present
        address = request.POST.get('delivery_address', address)
        date = request.POST.get('delivery_date', date)
        payment_method = request.POST.get('payment_method', payment_method)
        request.session['delivery_address'] = address
        request.session['delivery_date'] = date
        request.session['payment_method'] = payment_method
        order_number = "BRFN-" + str(request.user.id) + "-" + str(request.session.session_key)
        context = {
            "order_number": order_number,
            "address": address,
            "date": date,
            "payment": payment_method,
            "producers": producers,
            "subtotal": subtotal,
            "commission": commission,
            "total": total,
        }
        return render(request, "orders/confirmation.html", context)
    return render(request, "orders/payment.html", {"total": total, "cart_items": cart_items})