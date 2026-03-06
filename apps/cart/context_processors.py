def cart_item_count(request):
    # only show cart count to authenticated users
    if not request.user.is_authenticated:
        return {"cart_item_count": 0}

    cart = request.session.get("cart", {})


    try:
        count = sum(int(qty) for qty in cart.values())
    except Exception:
        count = 0

    return {"cart_item_count": count}