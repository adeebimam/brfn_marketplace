from .models import Cart

def cart_item_count(request):
    if not request.user.is_authenticated:
        return {"cart_item_count": 0}

    try:
        cart = Cart.objects.get(user=request.user)
        count = sum(item.quantity for item in cart.items.all())
    except Cart.DoesNotExist:
        count = 0

    return {"cart_item_count": count}