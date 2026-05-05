from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Profile
from apps.cart.models import Cart
from apps.marketplace.models import Product


class TC19CartPricingTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username="customer",
            email="customer@example.com",
            password="testpass123",
        )
        Profile.objects.create(
            user=self.customer,
            role=Profile.Role.CUSTOMER,
            contact_first_name="Test",
            contact_last_name="Customer",
        )

        self.producer = User.objects.create_user(
            username="producer",
            email="producer@example.com",
            password="testpass123",
        )
        Profile.objects.create(
            user=self.producer,
            role=Profile.Role.PRODUCER,
            business_name="Green Farm",
            is_verified=True,
        )

        self.product = Product.objects.create(
            producer=self.producer,
            name="Eggs",
            price=Decimal("10.00"),
            stock_quantity=10,
            is_surplus=True,
            surplus_discount_percent=30,
            surplus_stock_quantity=2,
            surplus_expires_at=timezone.now() + timedelta(days=1),
        )

    def test_cart_uses_discounted_price_for_active_surplus_deal(self):
        self.client.force_login(self.customer)

        response = self.client.post(
            reverse("cart:add", args=[self.product.id]),
            {"qty": 1},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "£7.00")
        self.assertNotContains(response, "£10.00</td>")

    def test_cart_splits_discounted_and_normal_price_when_surplus_stock_runs_out(self):
        self.client.force_login(self.customer)

        response = self.client.post(
            reverse("cart:add", args=[self.product.id]),
            {"qty": 4},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 at £7.00 deal price")
        self.assertContains(response, "2 at £10.00 normal price")
        self.assertContains(response, "£34.00")

    def test_cart_uses_normal_price_after_surplus_deal_expires(self):
        self.product.surplus_expires_at = timezone.now() - timedelta(minutes=5)
        self.product.save()

        self.client.force_login(self.customer)
        response = self.client.post(
            reverse("cart:add", args=[self.product.id]),
            {"qty": 2},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "£20.00")
        self.assertNotContains(response, "deal price")

    def test_product_list_quantity_field_is_accepted(self):
        self.client.force_login(self.customer)
        self.client.post(
            reverse("cart:add", args=[self.product.id]),
            {"quantity": 3},
            follow=True,
        )

        cart = Cart.objects.get(user=self.customer)
        cart_item = cart.items.get(product=self.product)
        self.assertEqual(cart_item.quantity, 3)
