from django.contrib.auth import get_user_model
from apps.accounts.models import Profile
from apps.marketplace.models import Order, ProducerOrder

User = get_user_model()


def is_admin(user):
    return (
        user.is_authenticated
        and hasattr(user, "profile")
        and user.profile.role == Profile.Role.ADMIN
    )


def get_allowed_message_recipients(user):
    if not user.is_authenticated or not hasattr(user, "profile"):
        return User.objects.none()

    profile = user.profile

    if profile.role == Profile.Role.ADMIN:
        return User.objects.exclude(id=user.id)

    allowed_user_ids = set()

    admins = User.objects.filter(profile__role=Profile.Role.ADMIN)
    allowed_user_ids.update(admins.values_list("id", flat=True))

    if profile.role == Profile.Role.PRODUCER:
        producer_orders = ProducerOrder.objects.filter(
            producer=user
        ).select_related("order__customer")

        customer_ids = producer_orders.values_list(
            "order__customer_id",
            flat=True
        )

        allowed_user_ids.update(customer_ids)

    else:
        customer_orders = Order.objects.filter(
            customer=user
        )

        producer_ids = ProducerOrder.objects.filter(
            order__in=customer_orders
        ).values_list("producer_id", flat=True)

        allowed_user_ids.update(producer_ids)

    return User.objects.filter(id__in=allowed_user_ids).exclude(id=user.id)