def cart_item_count(request):
    cart = request.session.get("cart", {})

    # common pattern: cart = {product_id: quantity, ...}
    try:
        count = sum(int(qty) for qty in cart.values())
    except Exception:
        count = 0

    return {"cart_item_count": count}