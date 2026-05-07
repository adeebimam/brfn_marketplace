from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.marketplace.models import (
    RecurringOrder,
    RecurringOrderItem,
    RecurringOrderInstance,
    RecurringOrderInstanceItem,
    Product,
)
from apps.cart.models import Cart, CartItem


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _next_weekday(from_date, weekday):
    """Return the next occurrence of `weekday` (0=Mon) after from_date."""
    days_ahead = weekday - from_date.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return from_date + timedelta(days=days_ahead)


# ---------------------------------------------------------------------------
# Step 1 — customer hits "Make this a recurring order" from the cart page
# Creates the RecurringOrder + items from current cart, then shows confirm page
# ---------------------------------------------------------------------------

@login_required
def recurring_order_setup(request):
    """GET: show the setup form. POST: create the recurring order template."""

    cart, _ = Cart.objects.get_or_create(user=request.user)
    items = cart.items.select_related("product", "product__producer")

    if not items.exists():
        messages.error(request, "Your cart is empty.")
        return redirect("cart:detail")

    # Build pricing summary for the template
    from decimal import Decimal
    cart_items = []
    total = Decimal("0.00")
    for item in items:
        line = item.product.price * item.quantity
        total += line
        cart_items.append({
            "product": item.product,
            "quantity": item.quantity,
            "line_total": line,
        })

    DAY_CHOICES = RecurringOrder.DAY_CHOICES

    if request.method == "POST":
        frequency = request.POST.get("frequency", "WEEKLY")
        order_day = int(request.POST.get("order_day", 0))
        delivery_day = int(request.POST.get("delivery_day", 2))
        delivery_address = request.POST.get("delivery_address", "").strip()
        delivery_postcode = request.POST.get("delivery_postcode", "").strip()
        name = request.POST.get("name", "").strip()

        if not delivery_address or not delivery_postcode:
            messages.error(request, "Delivery address and postcode are required.")
            return render(request, "marketplace/recurring_order_setup.html", {
                "cart_items": cart_items,
                "total": total,
                "day_choices": DAY_CHOICES,
                "frequency_choices": RecurringOrder.Frequency.choices,
            })

        # Create the template
        recurring = RecurringOrder.objects.create(
            customer=request.user,
            frequency=frequency,
            order_day=order_day,
            delivery_day=delivery_day,
            delivery_address=delivery_address,
            delivery_postcode=delivery_postcode,
            name=name,
            status=RecurringOrder.Status.ACTIVE,
        )

        # Copy cart items into the template
        for item in items:
            RecurringOrderItem.objects.create(
                recurring_order=recurring,
                product=item.product,
                quantity=item.quantity,
            )

        # Pre-generate the next instance so the customer can see it immediately
        _generate_next_instance(recurring)

        messages.success(
            request,
            f"Recurring order set up! Your first order will be generated on "
            f"{recurring.next_order_date.strftime('%A, %d %b %Y')}."
        )
        return redirect("marketplace:recurring_order_list")

    # Pre-fill address from profile if available
    initial_address = ""
    initial_postcode = ""
    if hasattr(request.user, "profile"):
        initial_address = request.user.profile.delivery_address or ""
        initial_postcode = request.user.profile.delivery_postcode or ""

    return render(request, "marketplace/recurring_order_setup.html", {
        "cart_items": cart_items,
        "total": total,
        "day_choices": DAY_CHOICES,
        "frequency_choices": RecurringOrder.Frequency.choices,
        "initial_address": initial_address,
        "initial_postcode": initial_postcode,
    })


# ---------------------------------------------------------------------------
# Step 2 — Management list page
# ---------------------------------------------------------------------------

@login_required
def recurring_order_list(request):
    recurring_orders = (
        RecurringOrder.objects
        .filter(customer=request.user)
        .prefetch_related("items__product", "instances")
        .order_by("-created_at")
    )

    for ro in recurring_orders:
        # Attach the next scheduled instance for display
        ro.next_instance = ro.instances.filter(
            status__in=[RecurringOrderInstance.Status.SCHEDULED, RecurringOrderInstance.Status.MODIFIED]
        ).first()

    return render(request, "marketplace/recurring_order_list.html", {
        "recurring_orders": recurring_orders,
    })


# ---------------------------------------------------------------------------
# Pause / Resume / Cancel
# ---------------------------------------------------------------------------

@login_required
def recurring_order_pause(request, pk):
    ro = get_object_or_404(RecurringOrder, pk=pk, customer=request.user)
    ro.status = RecurringOrder.Status.PAUSED
    ro.save()
    messages.info(request, "Recurring order paused.")
    return redirect("marketplace:recurring_order_list")


@login_required
def recurring_order_resume(request, pk):
    ro = get_object_or_404(RecurringOrder, pk=pk, customer=request.user)
    ro.status = RecurringOrder.Status.ACTIVE
    ro.save()
    messages.success(request, "Recurring order resumed.")
    return redirect("marketplace:recurring_order_list")


@login_required
def recurring_order_cancel(request, pk):
    ro = get_object_or_404(RecurringOrder, pk=pk, customer=request.user)
    if request.method == "POST":
        ro.status = RecurringOrder.Status.CANCELLED
        ro.save()
        messages.info(request, "Recurring order cancelled.")
        return redirect("marketplace:recurring_order_list")
    return render(request, "marketplace/recurring_order_confirm_cancel.html", {"ro": ro})


# ---------------------------------------------------------------------------
# Modify a single instance (not the template)
# ---------------------------------------------------------------------------

@login_required
def recurring_instance_modify(request, pk):
    instance = get_object_or_404(
        RecurringOrderInstance,
        pk=pk,
        recurring_order__customer=request.user,
        status__in=[RecurringOrderInstance.Status.SCHEDULED, RecurringOrderInstance.Status.MODIFIED],
    )

    # Seed instance items from template if none exist yet
    if not instance.items.exists():
        for template_item in instance.recurring_order.items.all():
            RecurringOrderInstanceItem.objects.create(
                instance=instance,
                product=template_item.product,
                quantity=template_item.quantity,
            )

    instance_items = instance.items.select_related("product")

    if request.method == "POST":
        for item in instance_items:
            key = f"qty_{item.product_id}"
            try:
                new_qty = int(request.POST.get(key, item.quantity))
            except (TypeError, ValueError):
                new_qty = item.quantity
            if new_qty <= 0:
                item.delete()
            else:
                item.quantity = new_qty
                item.save()

        instance.status = RecurringOrderInstance.Status.MODIFIED
        instance.save()
        messages.success(
            request,
            "Next order updated. The template for future orders is unchanged."
        )
        return redirect("marketplace:recurring_order_list")

    return render(request, "marketplace/recurring_instance_modify.html", {
        "instance": instance,
        "instance_items": instance_items,
    })


# ---------------------------------------------------------------------------
# Internal helper — generate the next scheduled instance for a RecurringOrder
# ---------------------------------------------------------------------------

def _generate_next_instance(recurring_order):
    today = date.today()
    # Next order_day occurrence
    next_order = _next_weekday(today, recurring_order.order_day)
    if recurring_order.frequency == RecurringOrder.Frequency.FORTNIGHTLY:
        next_order += timedelta(weeks=1)

    # Delivery day must be after order day
    next_delivery = _next_weekday(next_order, recurring_order.delivery_day)

    # Avoid duplicates
    exists = RecurringOrderInstance.objects.filter(
        recurring_order=recurring_order,
        scheduled_date=next_order,
    ).exists()

    if not exists:
        instance = RecurringOrderInstance.objects.create(
            recurring_order=recurring_order,
            scheduled_date=next_order,
            delivery_date=next_delivery,
            status=RecurringOrderInstance.Status.SCHEDULED,
        )
        # Copy template items into this instance
        for template_item in recurring_order.items.all():
            RecurringOrderInstanceItem.objects.create(
                instance=instance,
                product=template_item.product,
                quantity=template_item.quantity,
            )
        recurring_order.next_order_date = next_order
        recurring_order.save(update_fields=["next_order_date"])
        return instance

    return None