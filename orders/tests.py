from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Profile
from apps.marketplace.models import Order, OrderItem, ProducerOrder, ProducerOrderStatusHistory, Product
from apps.marketplace.services import update_producer_order_status


class OrderHistoryTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username="customer_history",
            email="customer_history@example.com",
            password="testpass123",
        )
        Profile.objects.create(
            user=self.customer,
            role=Profile.Role.CUSTOMER,
            contact_first_name="Robert",
            contact_last_name="Johnson",
        )

        self.producer = User.objects.create_user(
            username="producer_history",
            email="producer_history@example.com",
            password="testpass123",
        )
        Profile.objects.create(
            user=self.producer,
            role=Profile.Role.PRODUCER,
            business_name="Valley Farm",
            is_verified=True,
        )

        self.product = Product.objects.create(
            producer=self.producer,
            name="Organic Free Range Eggs",
            price=Decimal("3.50"),
            stock_quantity=40,
        )

        self.order = Order.objects.create(
            customer=self.customer,
            delivery_address="1 High Street, Bristol",
            delivery_postcode="BS1 5JG",
        )
        self.producer_order = ProducerOrder.objects.create(
            order=self.order,
            producer=self.producer,
            delivery_date=date.today() + timedelta(days=2),
            total_value=Decimal("7.00"),
        )
        OrderItem.objects.create(
            producer_order=self.producer_order,
            product=self.product,
            quantity=2,
            unit_price=Decimal("3.50"),
        )
        ProducerOrderStatusHistory.objects.create(
            producer_order=self.producer_order,
            old_status="",
            new_status=ProducerOrder.Status.PENDING,
            note="Order created",
            changed_by=self.customer,
        )

    def test_customer_can_view_order_history(self):
        self.client.force_login(self.customer)

        response = self.client.get(reverse("orders:history"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Order #")
        self.assertContains(response, "Organic Free Range Eggs")
        self.assertContains(response, "Pending")

    def test_customer_sees_producer_status_updates_in_order_detail(self):
        update_producer_order_status(
            producer_order=self.producer_order,
            new_status=ProducerOrder.Status.CONFIRMED,
            changed_by=self.producer,
            note="Packed and confirmed.",
        )

        self.client.force_login(self.customer)
        response = self.client.get(reverse("orders:detail", args=[self.order.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Producer confirmed")
        self.assertContains(response, "Packed and confirmed.")
        self.assertContains(response, "Confirmed")
