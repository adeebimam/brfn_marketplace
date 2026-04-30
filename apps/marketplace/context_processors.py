from django.utils import timezone

from .models import Product


def active_surplus_deal_count(request):
    count = Product.objects.filter(
        is_active=True,
        is_surplus=True,
        surplus_stock_quantity__gt=0,
        stock_quantity__gt=0,
        surplus_expires_at__gt=timezone.now(),
    ).count()

    return {"active_surplus_deal_count": count}
