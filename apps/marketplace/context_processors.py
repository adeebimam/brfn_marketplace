from django.utils import timezone

from apps.accounts.models import Profile

from .models import FavouriteProducer, Product, SurplusDealNotification
from .services import expire_surplus_deals


def active_surplus_deal_count(request):
    expire_surplus_deals()

    count = Product.objects.filter(
        is_active=True,
        is_surplus=True,
        surplus_stock_quantity__gt=0,
        stock_quantity__gt=0,
        surplus_expires_at__gt=timezone.now(),
    ).count()

    latest_favourite_surplus_notification = None
    unread_favourite_surplus_notification_count = 0

    if request.user.is_authenticated:
        profile = getattr(request.user, "profile", None)
        if profile and profile.role in {
            Profile.Role.CUSTOMER,
            Profile.Role.COMMUNITY_GROUP,
            Profile.Role.RESTAURANT,
        }:
            favourite_producer_ids = FavouriteProducer.objects.filter(
                customer=request.user
            ).values_list("producer_id", flat=True)
            notifications = (
                SurplusDealNotification.objects
                .filter(
                    customer=request.user,
                    producer_id__in=favourite_producer_ids,
                    is_read=False,
                )
                .select_related("producer", "product")
            )
            latest_favourite_surplus_notification = notifications.first()
            unread_favourite_surplus_notification_count = notifications.count()

    return {
        "active_surplus_deal_count": count,
        "latest_favourite_surplus_notification": latest_favourite_surplus_notification,
        "unread_favourite_surplus_notification_count": unread_favourite_surplus_notification_count,
    }
