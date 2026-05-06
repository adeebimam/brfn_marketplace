from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Profile
from apps.cart.models import Cart
from apps.marketplace.models import Product, Order, ProducerOrder, OrderItem


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


class TC017CommunityGroupBulkOrderTests(TestCase):
    def setUp(self):
        self.community_user = User.objects.create_user(
            username="stmarys_school",
            email="catering@stmarys-school.org.uk",
            password="testpass123",
        )
        Profile.objects.create(
            user=self.community_user,
            role=Profile.Role.COMMUNITY_GROUP,
            business_name="St. Mary's School",
            contact_first_name="Kitchen",
            contact_last_name="Manager",
            phone="01170000000",
            address="St. Mary's School, Bristol",
            postcode="BS1 1AA",
            delivery_address="St. Mary's School Kitchen, Bristol",
            delivery_postcode="BS1 1AA",
        )

        self.producer_one = self._create_producer(
            username="potato_farm",
            email="potatoes@example.com",
            business_name="Potato Farm",
        )
        self.producer_two = self._create_producer(
            username="dairy_farm",
            email="milk@example.com",
            business_name="Dairy Farm",
        )
        self.producer_three = self._create_producer(
            username="veg_farm",
            email="carrots@example.com",
            business_name="Vegetable Farm",
        )

        self.potatoes = Product.objects.create(
            producer=self.producer_one,
            name="Potatoes",
            price=Decimal("1.20"),
            unit="kg",
            stock_quantity=100,
            is_active=True,
        )

        self.milk = Product.objects.create(
            producer=self.producer_two,
            name="Milk",
            price=Decimal("1.50"),
            unit="litre",
            stock_quantity=80,
            is_active=True,
        )

        self.carrots = Product.objects.create(
            producer=self.producer_three,
            name="Carrots",
            price=Decimal("0.90"),
            unit="kg",
            stock_quantity=60,
            is_active=True,
        )

    def _create_producer(self, username, email, business_name):
        producer = User.objects.create_user(
            username=username,
            email=email,
            password="testpass123",
        )
        Profile.objects.create(
            user=producer,
            role=Profile.Role.PRODUCER,
            business_name=business_name,
            contact_first_name=business_name,
            contact_last_name="Contact",
            phone="01171111111",
            postcode="BS2 2BB",
            is_verified=True,
        )
        return producer

    def test_community_group_can_place_bulk_order_from_multiple_producers(self):
        self.client.force_login(self.community_user)

        self.client.post(
            reverse("cart:add", args=[self.potatoes.id]),
            {"qty": 50},
            follow=True,
        )
        self.client.post(
            reverse("cart:add", args=[self.milk.id]),
            {"qty": 30},
            follow=True,
        )
        self.client.post(
            reverse("cart:add", args=[self.carrots.id]),
            {"qty": 20},
            follow=True,
        )

        cart = Cart.objects.get(user=self.community_user)
        self.assertEqual(cart.items.count(), 3)

        delivery_date = (timezone.now().date() + timedelta(days=5)).isoformat()
        special_instructions = "Delivery to kitchen entrance, contact kitchen manager"

        checkout_response = self.client.post(
            reverse("cart:checkout"),
            {
                "delivery_address": "St. Mary's School Kitchen, Bristol",
                "delivery_postcode": "BS1 1AA",
                "delivery_date": delivery_date,
                "payment_method": "stripe",
                "special_instructions": special_instructions,
            },
            follow=True,
        )

        self.assertEqual(checkout_response.status_code, 200)

        payment_response = self.client.post(
            "/orders/payment/",
            {
                "delivery_address": "St. Mary's School Kitchen, Bristol",
                "delivery_date": delivery_date,
                "payment_method": "stripe",
            },
            follow=True,
        )
        print("PAYMENT REDIRECTS: ", payment_response.redirect_chain)
        print("PAYMENT CONTENT: ", payment_response.content.decode()[:2000])
        print("MESSAGES:", [str(m) for m in payment_response.wsgi_request._messages]) 
        print("SESSION: ", dict(self.client.session))

        self.assertEqual(checkout_response.status_code, 200)

        order = Order.objects.get(customer=self.community_user)

        self.assertEqual(order.delivery_address, "St. Mary's School Kitchen, Bristol")
        self.assertEqual(order.delivery_postcode, "BS1 1AA")
        self.assertEqual(order.special_instructions, special_instructions)

        producer_orders = ProducerOrder.objects.filter(order=order)
        self.assertEqual(producer_orders.count(), 3)

        self.assertEqual(
            set(producer_orders.values_list("producer", flat=True)),
            {self.producer_one.id, self.producer_two.id, self.producer_three.id},
        )

        self.assertEqual(OrderItem.objects.filter(producer_order__order=order).count(), 3)

        self.potatoes.refresh_from_db()
        self.milk.refresh_from_db()
        self.carrots.refresh_from_db()

        self.assertEqual(self.potatoes.stock_quantity, 50)
        self.assertEqual(self.milk.stock_quantity, 50)
        self.assertEqual(self.carrots.stock_quantity, 40)

        cart.refresh_from_db()
        self.assertEqual(cart.items.count(), 0)

        self.assertContains(payment_response, "Producer breakdown")
        self.assertContains(payment_response, "Potatoes")
        self.assertContains(payment_response, "Milk")
        self.assertContains(payment_response, "Carrots")
        self.assertContains(payment_response, special_instructions)
    def test_bulk_buyer_minimum_quantity_enforced(self):
        """Community group cannot order less than 5 units per item."""
        self.client.force_login(self.community_user)
        resp = self.client.post(
            reverse("cart:add", args=[self.potatoes.id]),
            {"qty": 3},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        from apps.cart.models import Cart
        cart, _ = Cart.objects.get_or_create(user=self.community_user)
        self.assertEqual(cart.items.count(), 0)

    def test_bulk_buyer_can_order_exactly_minimum_quantity(self):
        """Community group can order exactly 5 units."""
        self.client.force_login(self.community_user)
        resp = self.client.post(
            reverse("cart:add", args=[self.potatoes.id]),
            {"qty": 5},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        from apps.cart.models import Cart
        cart = Cart.objects.get(user=self.community_user)
        self.assertEqual(cart.items.count(), 1)
        self.assertEqual(cart.items.first().quantity, 5)

    def test_bulk_buyer_can_order_above_stock(self):
        """Community group can order above stock — order goes through with warning."""
        self.client.force_login(self.community_user)
        resp = self.client.post(
            reverse("cart:add", args=[self.potatoes.id]),
            {"qty": 150},  # potatoes has 100 in stock
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        from apps.cart.models import Cart
        cart = Cart.objects.get(user=self.community_user)
        self.assertEqual(cart.items.count(), 1)
        self.assertEqual(cart.items.first().quantity, 150)

    def test_regular_customer_cannot_order_above_stock(self):
        """Regular customer is blocked from ordering above stock."""
        customer = User.objects.create_user(
            username="regular_customer",
            email="regular@example.com",
            password="testpass123",
        )
        from apps.accounts.models import Profile
        Profile.objects.create(
            user=customer,
            role=Profile.Role.CUSTOMER,
            contact_first_name="Regular",
            contact_last_name="Customer",
        )
        self.client.force_login(customer)
        resp = self.client.post(
            reverse("cart:add", args=[self.potatoes.id]),
            {"qty": 150},  # above stock of 100
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        from apps.cart.models import Cart
        cart, _ = Cart.objects.get_or_create(user=customer)
        self.assertEqual(cart.items.count(), 0)

    def test_bulk_discount_applied_at_checkout(self):
        """10% bulk discount is applied for community group at checkout."""
        self.client.force_login(self.community_user)
        self.client.post(
            reverse("cart:add", args=[self.potatoes.id]),
            {"qty": 10},
            follow=True,
        )
        resp = self.client.get(reverse("cart:checkout"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["is_bulk_buyer"])
        self.assertGreater(resp.context["bulk_discount"], 0)
        # subtotal = 10 * 1.20 = 12.00, discount = 1.20
        from decimal import Decimal
        self.assertEqual(resp.context["bulk_discount"], Decimal("1.20"))
        self.assertEqual(resp.context["discounted_subtotal"], Decimal("10.80"))

    def test_restaurant_also_gets_bulk_discount(self):
        """Restaurant role also gets 10% bulk discount."""
        restaurant_user = User.objects.create_user(
            username="restaurant_user",
            email="restaurant@example.com",
            password="testpass123",
        )
        from apps.accounts.models import Profile
        Profile.objects.create(
            user=restaurant_user,
            role=Profile.Role.RESTAURANT,
            business_name="Test Restaurant",
            contact_first_name="Chef",
            contact_last_name="Cook",
        )
        self.client.force_login(restaurant_user)
        self.client.post(
            reverse("cart:add", args=[self.potatoes.id]),
            {"qty": 10},
            follow=True,
        )
        resp = self.client.get(reverse("cart:checkout"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["is_bulk_buyer"])
        self.assertGreater(resp.context["bulk_discount"], 0)

    def test_regular_customer_gets_no_bulk_discount(self):
        """Regular customer does not get bulk discount."""
        customer = User.objects.create_user(
            username="nodiscount_customer",
            email="nodiscount@example.com",
            password="testpass123",
        )
        from apps.accounts.models import Profile
        Profile.objects.create(
            user=customer,
            role=Profile.Role.CUSTOMER,
            contact_first_name="No",
            contact_last_name="Discount",
        )
        self.client.force_login(customer)
        self.client.post(
            reverse("cart:add", args=[self.potatoes.id]),
            {"qty": 10},
            follow=True,
        )
        resp = self.client.get(reverse("cart:checkout"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["is_bulk_buyer"])
        from decimal import Decimal
        self.assertEqual(resp.context["bulk_discount"], Decimal("0.00"))