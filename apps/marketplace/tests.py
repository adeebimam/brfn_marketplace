"""
TC-016: Seasonal Product Availability
──────────────────────────────────────
Validates that producers can manage seasonal availability and that
customers see accurate seasonal indicators. Out-of-season products
are hidden from customers and cannot be ordered.

Covers:
  • Producer can set seasonal date ranges (month-to-month)
  • Products marked year-round have no seasonal restrictions
  • "In Season" badge displayed for in-season products
  • Seasonal date range displayed (e.g. "June – August")
  • Out-of-season products are hidden from the product list
  • Out-of-season products return 404 for customer detail view
  • Customers cannot add out-of-season products to cart
  • System automatically determines availability based on current date
  • Wrap-around ranges work (e.g. November – February)
"""
from datetime import date
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Profile
from apps.marketplace.forms import ProductForm
from apps.marketplace.models import (
    Allergen,
    Category,
    FavouriteProducer,
    MONTH_NAMES,
    Order,
    OrderItem,
    Product,
    ProducerOrder,
    SurplusAnalyticsRecord,
    SurplusDealNotification,
)
from apps.marketplace.services import expire_surplus_deals, notify_favourite_customers_about_surplus


# ─── helpers ────────────────────────────────────────────────────────
def _create_user(username, email, password, role, **extra_profile):
    user = User.objects.create_user(username=username, email=email, password=password)
    Profile.objects.create(user=user, role=role, **extra_profile)
    return user


def _create_product(producer, name, season="ALL", from_month=None, to_month=None, **kwargs):
    defaults = {
        "price": "3.50",
        "stock_quantity": 10,
        "is_active": True,
    }
    defaults.update(kwargs)
    return Product.objects.create(
        producer=producer,
        name=name,
        season=season,
        available_from_month=from_month,
        available_to_month=to_month,
        **defaults,
    )


# ═══════════════════════════════════════════════════════════════════
# TC-16.1 – Model: is_in_season / season_label / is_year_round
# ═══════════════════════════════════════════════════════════════════
class TC16_ModelTests(TestCase):
    def setUp(self):
        self.producer = _create_user(
            "farmer", "farmer@example.com", "Str0ng!Pass99", Profile.Role.PRODUCER,
            business_name="Green Farm",
        )

    # ── year-round ─────────────────────────────────────────
    def test_year_round_always_in_season(self):
        p = _create_product(self.producer, "Stored Potatoes", season="ALL")
        self.assertTrue(p.is_year_round)
        self.assertTrue(p.is_in_season(date(2026, 1, 15)))
        self.assertTrue(p.is_in_season(date(2026, 7, 15)))

    def test_year_round_label(self):
        p = _create_product(self.producer, "Stored Potatoes", season="ALL")
        self.assertEqual(p.season_label, "Available Year-Round")

    # ── simple range (June–August) ─────────────────────────
    def test_in_season_simple_range(self):
        """Strawberries: June – August → in season in July."""
        p = _create_product(self.producer, "Strawberries", season="SUMMER", from_month=6, to_month=8)
        self.assertFalse(p.is_year_round)
        self.assertTrue(p.is_in_season(date(2026, 6, 1)))   # June
        self.assertTrue(p.is_in_season(date(2026, 7, 15)))  # July
        self.assertTrue(p.is_in_season(date(2026, 8, 31)))  # August
        self.assertFalse(p.is_in_season(date(2026, 5, 31))) # May
        self.assertFalse(p.is_in_season(date(2026, 9, 1)))  # September

    def test_seasonal_label_simple_range(self):
        p = _create_product(self.producer, "Strawberries", season="SUMMER", from_month=6, to_month=8)
        self.assertEqual(p.season_label, "June – August")

    # ── wrap-around range (November–February) ──────────────
    def test_in_season_wrap_around_range(self):
        """Winter veg: November – February → in season in Jan, out in June."""
        p = _create_product(self.producer, "Parsnips", season="WINTER", from_month=11, to_month=2)
        self.assertTrue(p.is_in_season(date(2026, 11, 1)))  # November
        self.assertTrue(p.is_in_season(date(2026, 12, 15))) # December
        self.assertTrue(p.is_in_season(date(2026, 1, 10)))  # January
        self.assertTrue(p.is_in_season(date(2026, 2, 28)))  # February
        self.assertFalse(p.is_in_season(date(2026, 3, 1)))  # March
        self.assertFalse(p.is_in_season(date(2026, 6, 15))) # June
        self.assertFalse(p.is_in_season(date(2026, 10, 31)))# October

    def test_seasonal_label_wrap_around(self):
        p = _create_product(self.producer, "Parsnips", season="WINTER", from_month=11, to_month=2)
        self.assertEqual(p.season_label, "November – February")

    # ── single-month season ────────────────────────────────
    def test_single_month_season(self):
        """Asparagus: May only."""
        p = _create_product(self.producer, "Asparagus", season="SPRING", from_month=5, to_month=5)
        self.assertTrue(p.is_in_season(date(2026, 5, 15)))
        self.assertFalse(p.is_in_season(date(2026, 4, 30)))
        self.assertFalse(p.is_in_season(date(2026, 6, 1)))


# ═══════════════════════════════════════════════════════════════════
# TC-16.2 – Producer form: setting seasonal availability
# ═══════════════════════════════════════════════════════════════════
class TC16_ProducerFormTests(TestCase):
    def setUp(self):
        self.password = "Str0ng!Pass99"
        self.producer = _create_user(
            "farmer", "farmer@example.com", self.password, Profile.Role.PRODUCER,
            business_name="Green Farm",
        )
        self.category = Category.objects.create(name="Fruit")
        self.client.force_login(self.producer)

    def test_create_seasonal_product(self):
        """Producer sets Strawberries as Summer, June–August."""
        resp = self.client.post("/producer/products/new/", {
            "category": self.category.pk,
            "name": "Strawberries",
            "description": "Fresh summer strawberries",
            "price": "4.50",
            "stock_quantity": 50,
            "season": "SUMMER",
            "available_from_month": 6,
            "available_to_month": 8,
        })
        self.assertEqual(resp.status_code, 302)
        p = Product.objects.get(name="Strawberries")
        self.assertEqual(p.season, "SUMMER")
        self.assertEqual(p.available_from_month, 6)
        self.assertEqual(p.available_to_month, 8)

    def test_create_year_round_product(self):
        """Producer sets Stored Potatoes as All Season (no months)."""
        resp = self.client.post("/producer/products/new/", {
            "category": self.category.pk,
            "name": "Stored Potatoes",
            "description": "Available year-round",
            "price": "2.00",
            "stock_quantity": 100,
            "season": "ALL",
        })
        self.assertEqual(resp.status_code, 302)
        p = Product.objects.get(name="Stored Potatoes")
        self.assertEqual(p.season, "ALL")
        self.assertIsNone(p.available_from_month)
        self.assertIsNone(p.available_to_month)
        self.assertTrue(p.is_year_round)

    def test_seasonal_requires_both_months(self):
        """Setting season != ALL without both months → form error."""
        resp = self.client.post("/producer/products/new/", {
            "category": self.category.pk,
            "name": "Incomplete Seasonal",
            "description": "",
            "price": "3.00",
            "stock_quantity": 10,
            "season": "SUMMER",
            "available_from_month": 6,
            # missing available_to_month
        })
        self.assertEqual(resp.status_code, 200)  # re-rendered with errors
        self.assertFalse(Product.objects.filter(name="Incomplete Seasonal").exists())

    def test_edit_product_to_change_season(self):
        """Edit existing product and change its seasonal range."""
        p = _create_product(self.producer, "Apples", season="ALL", category=self.category)
        resp = self.client.post(f"/producer/products/{p.pk}/edit/", {
            "category": self.category.pk,
            "name": "Apples",
            "description": "Autumn apples",
            "price": "3.50",
            "stock_quantity": 30,
            "season": "AUTUMN",
            "available_from_month": 9,
            "available_to_month": 11,
        })
        self.assertEqual(resp.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.season, "AUTUMN")
        self.assertEqual(p.available_from_month, 9)
        self.assertEqual(p.available_to_month, 11)


# ═══════════════════════════════════════════════════════════════════
# TC-16.3 – Customer view: seasonal indicators & auto-hiding
# ═══════════════════════════════════════════════════════════════════
class TC16_CustomerViewTests(TestCase):
    def setUp(self):
        self.password = "Str0ng!Pass99"
        self.producer = _create_user(
            "farmer", "farmer@example.com", self.password, Profile.Role.PRODUCER,
            business_name="Green Farm",
        )
        self.customer = _create_user(
            "shopper", "shopper@example.com", self.password, Profile.Role.CUSTOMER,
        )
        cat = Category.objects.create(name="Produce")

        # Strawberries: Summer, June–August
        self.strawberries = _create_product(
            self.producer, "Strawberries", season="SUMMER",
            from_month=6, to_month=8, category=cat,
        )
        # Stored Potatoes: Year-round
        self.potatoes = _create_product(
            self.producer, "Stored Potatoes", season="ALL", category=cat,
        )
        # Parsnips: Winter, Nov–Feb
        self.parsnips = _create_product(
            self.producer, "Parsnips", season="WINTER",
            from_month=11, to_month=2, category=cat,
        )

    # ── product list filtering by date ─────────────────────
    @patch("apps.marketplace.views.date")
    def test_in_season_products_shown_in_july(self, mock_date):
        """In July: Strawberries (Jun-Aug) and Potatoes shown; Parsnips (Nov-Feb) hidden."""
        mock_date.today.return_value = date(2026, 7, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        resp = self.client.get("/products/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("Strawberries", content)
        self.assertIn("Stored Potatoes", content)
        self.assertNotIn("Parsnips", content)

    @patch("apps.marketplace.views.date")
    def test_in_season_products_shown_in_december(self, mock_date):
        """In December: Parsnips (Nov-Feb) and Potatoes shown; Strawberries (Jun-Aug) hidden."""
        mock_date.today.return_value = date(2026, 12, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        resp = self.client.get("/products/")
        content = resp.content.decode()
        self.assertIn("Parsnips", content)
        self.assertIn("Stored Potatoes", content)
        self.assertNotIn("Strawberries", content)

    @patch("apps.marketplace.views.date")
    def test_in_season_badge_shown(self, mock_date):
        """Seasonal product that is in season shows 'In Season' badge."""
        mock_date.today.return_value = date(2026, 7, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        resp = self.client.get("/products/")
        content = resp.content.decode()
        self.assertIn("In Season", content)

    @patch("apps.marketplace.views.date")
    def test_year_round_no_seasonal_badge(self, mock_date):
        """Year-round products do NOT show an 'In Season' badge."""
        mock_date.today.return_value = date(2026, 7, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        resp = self.client.get("/products/")
        content = resp.content.decode()
        self.assertIn("Available Year-Round", content)

    @patch("apps.marketplace.views.date")
    def test_seasonal_date_range_shown(self, mock_date):
        """Seasonal product shows its date range, e.g. 'June – August'."""
        mock_date.today.return_value = date(2026, 7, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        resp = self.client.get("/products/")
        content = resp.content.decode()
        self.assertIn("June", content)
        self.assertIn("August", content)

    # ── product detail: out-of-season → 404 for customer ──
    @patch("apps.marketplace.models.date")
    def test_out_of_season_product_detail_404(self, mock_date):
        """Customer trying to view Strawberries in December gets 404."""
        mock_date.today.return_value = date(2026, 12, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        self.client.force_login(self.customer)
        resp = self.client.get(f"/products/{self.strawberries.pk}/")
        self.assertEqual(resp.status_code, 404)

    @patch("apps.marketplace.models.date")
    def test_producer_can_view_own_out_of_season_product(self, mock_date):
        """Producer can still see their own out-of-season product."""
        mock_date.today.return_value = date(2026, 12, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        self.client.force_login(self.producer)
        resp = self.client.get(f"/products/{self.strawberries.pk}/")
        self.assertEqual(resp.status_code, 200)

    # ── cart: cannot order out-of-season ───────────────────
    @patch("apps.marketplace.models.date")
    def test_customer_cannot_add_out_of_season_to_cart(self, mock_date):
        """Adding out-of-season product to cart is rejected."""
        mock_date.today.return_value = date(2026, 12, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        self.client.force_login(self.customer)
        resp = self.client.post(f"/cart/add/{self.strawberries.pk}/", {"qty": 1})
        self.assertEqual(resp.status_code, 302)
        from apps.cart.models import Cart
        cart, _ = Cart.objects.get_or_create(user=self.customer)
        self.assertEqual(cart.items.count(), 0)

    @patch("apps.marketplace.models.date")
    def test_customer_can_add_in_season_to_cart(self, mock_date):
        """Adding in-season product to cart works."""
        mock_date.today.return_value = date(2026, 7, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        self.client.force_login(self.customer)
        resp = self.client.post(f"/cart/add/{self.strawberries.pk}/", {"qty": 1})
        self.assertEqual(resp.status_code, 302)
        from apps.cart.models import Cart
        cart = Cart.objects.get(user=self.customer)
        self.assertEqual(cart.items.count(), 1)
        self.assertEqual(cart.items.first().product, self.strawberries)

    @patch("apps.marketplace.models.date")
    def test_customer_can_always_add_year_round_to_cart(self, mock_date):
        """Year-round products can be added to cart at any time."""
        mock_date.today.return_value = date(2026, 12, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        self.client.force_login(self.customer)
        resp = self.client.post(f"/cart/add/{self.potatoes.pk}/", {"qty": 1})
        self.assertEqual(resp.status_code, 302)
        from apps.cart.models import Cart
        cart = Cart.objects.get(user=self.customer)
        self.assertEqual(cart.items.count(), 1)


# ═══════════════════════════════════════════════════════════════════
# TC-16.4 – Producer dashboard: seasonal status badges
# ═══════════════════════════════════════════════════════════════════
class TC16_ProducerDashboardTests(TestCase):
    def setUp(self):
        self.password = "Str0ng!Pass99"
        self.producer = _create_user(
            "farmer", "farmer@example.com", self.password, Profile.Role.PRODUCER,
            business_name="Green Farm",
        )
        self.client.force_login(self.producer)
        cat = Category.objects.create(name="Produce")
        self.strawberries = _create_product(
            self.producer, "Strawberries", season="SUMMER",
            from_month=6, to_month=8, category=cat,
        )
        self.potatoes = _create_product(
            self.producer, "Stored Potatoes", season="ALL", category=cat,
        )

    @patch("apps.marketplace.models.date")
    def test_out_of_season_badge_on_producer_list(self, mock_date):
        """In December, Strawberries shows 'Out of Season' on producer list."""
        mock_date.today.return_value = date(2026, 12, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        resp = self.client.get("/producer/products/")
        content = resp.content.decode()
        self.assertIn("Out of Season", content)

    @patch("apps.marketplace.models.date")
    def test_in_season_badge_on_producer_list(self, mock_date):
        """In July, Strawberries shows 'In Season' on producer list."""
        mock_date.today.return_value = date(2026, 7, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

        resp = self.client.get("/producer/products/")
        content = resp.content.decode()
        self.assertIn("In Season", content)

    def test_season_range_shown_on_producer_list(self):
        """Producer list shows the month range for seasonal products."""
        resp = self.client.get("/producer/products/")
        content = resp.content.decode()
        self.assertIn("June", content)
        self.assertIn("August", content)


class TC19SurplusDealTests(TestCase):
    def setUp(self):
        self.producer = _create_user(
            "producer", "producer@example.com", "Str0ng!Pass99", Profile.Role.PRODUCER,
            business_name="Surplus Farm",
            is_verified=True,
        )
        self.customer = _create_user(
            "customer", "customer@example.com", "Str0ng!Pass99", Profile.Role.CUSTOMER,
            contact_first_name="Casey",
            contact_last_name="Customer",
        )
        self.category = Category.objects.create(name="Dairy")
        self.no_common_allergens = Allergen.objects.create(name="No common allergens")

    def test_product_stores_discount_amount_for_surplus_deal(self):
        product = Product.objects.create(
            producer=self.producer,
            category=self.category,
            name="Eggs",
            description="Fresh eggs",
            price=Decimal("10.00"),
            stock_quantity=8,
            is_surplus=True,
            surplus_discount_percent=30,
            surplus_stock_quantity=3,
            surplus_expires_at=timezone.now() + timedelta(days=1),
        )

        self.assertEqual(product.surplus_discount_amount, Decimal("3.00"))
        self.assertEqual(product.surplus_discounted_price, Decimal("7.00"))

    def test_surplus_form_rejects_past_expiry_datetime(self):
        form = ProductForm(data={
            "category": self.category.pk,
            "name": "Eggs",
            "description": "Fresh eggs",
            "price": "10.00",
            "unit": "pack",
            "stock_quantity": 8,
            "estimated_unit_weight_kg": "0.50",
            "low_stock_threshold": 2,
            "season": "ALL",
            "allergens": [self.no_common_allergens.pk],
            "is_surplus": "on",
            "surplus_discount_percent": 30,
            "surplus_stock_quantity": 3,
            "surplus_expires_at": (timezone.localtime() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            "surplus_note": "Move quickly",
        })

        self.assertFalse(form.is_valid())
        self.assertIn("future", str(form.errors))

    def test_expired_surplus_deal_falls_back_to_normal_price_on_product_detail(self):
        product = Product.objects.create(
            producer=self.producer,
            category=self.category,
            name="Eggs",
            description="Fresh eggs",
            price=Decimal("10.00"),
            stock_quantity=8,
            is_surplus=True,
            surplus_discount_percent=30,
            surplus_stock_quantity=3,
            surplus_expires_at=timezone.now() - timedelta(hours=1),
        )

        response = self.client.get(reverse("marketplace:product_detail", args=[product.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Last Minute Deal")
        self.assertContains(response, "£10.00")

    def test_expired_surplus_deal_is_cleared_from_database_on_product_list_visit(self):
        product = Product.objects.create(
            producer=self.producer,
            category=self.category,
            name="Milk",
            description="Fresh milk",
            price=Decimal("4.00"),
            stock_quantity=5,
            is_surplus=True,
            surplus_discount_percent=25,
            surplus_stock_quantity=2,
            surplus_expires_at=timezone.now() - timedelta(minutes=10),
            surplus_note="Use soon",
        )

        response = self.client.get(reverse("marketplace:product_list"))

        self.assertEqual(response.status_code, 200)

        product.refresh_from_db()
        self.assertFalse(product.is_surplus)
        self.assertIsNone(product.surplus_discount_percent)
        self.assertEqual(product.surplus_discounted_price, Decimal("0.00"))
        self.assertEqual(product.surplus_discount_amount, Decimal("0.00"))
        self.assertEqual(product.surplus_stock_quantity, 0)
        self.assertIsNone(product.surplus_expires_at)
        self.assertEqual(product.surplus_note, "")


class TC19SurplusNotificationTests(TestCase):
    def setUp(self):
        self.password = "Str0ng!Pass99"
        self.customer = _create_user(
            "buyer_alerts",
            "buyer_alerts@example.com",
            self.password,
            Profile.Role.CUSTOMER,
            contact_first_name="Buyer",
            contact_last_name="Alerts",
        )
        self.producer = _create_user(
            "producer_alerts",
            "producer_alerts@example.com",
            self.password,
            Profile.Role.PRODUCER,
            business_name="Alerts Farm",
            is_verified=True,
        )
        self.category = Category.objects.create(name="Surplus Alerts")

    def test_customer_can_favourite_producer(self):
        self.client.force_login(self.customer)

        response = self.client.post(
            reverse("marketplace:toggle_favourite_producer", args=[self.producer.id]),
            {"next": reverse("marketplace:product_list")},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            FavouriteProducer.objects.filter(
                customer=self.customer,
                producer=self.producer,
            ).exists()
        )

    def test_surplus_alert_page_shows_notification_for_favourite_producer(self):
        FavouriteProducer.objects.create(
            customer=self.customer,
            producer=self.producer,
        )
        product = Product.objects.create(
            producer=self.producer,
            category=self.category,
            name="Discounted Lettuce",
            description="Surplus lettuce",
            price=Decimal("4.00"),
            stock_quantity=8,
            is_surplus=True,
            surplus_discount_percent=25,
            surplus_stock_quantity=3,
            surplus_expires_at=timezone.now() + timedelta(days=1),
        )

        created_count = notify_favourite_customers_about_surplus(product)

        self.assertEqual(created_count, 1)
        self.assertTrue(
            SurplusDealNotification.objects.filter(
                customer=self.customer,
                producer=self.producer,
                product=product,
            ).exists()
        )

        self.client.force_login(self.customer)
        response = self.client.get(reverse("marketplace:surplus_notifications"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Surplus Alerts")
        self.assertContains(response, "Discounted Lettuce")

    def test_favourite_offers_filter_shows_favourite_producer_deals(self):
        FavouriteProducer.objects.create(
            customer=self.customer,
            producer=self.producer,
        )
        Product.objects.create(
            producer=self.producer,
            category=self.category,
            name="Favourite Carrots",
            description="Surplus carrots",
            price=Decimal("3.50"),
            stock_quantity=10,
            is_surplus=True,
            surplus_discount_percent=20,
            surplus_stock_quantity=4,
            surplus_expires_at=timezone.now() + timedelta(days=1),
        )

        self.client.force_login(self.customer)
        response = self.client.get(
            reverse("marketplace:surplus_deals") + "?favourites=1"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Favourite Producer Deals")
        self.assertContains(response, "Favourite Carrots")

    def test_removing_favourite_producer_clears_existing_notifications(self):
        FavouriteProducer.objects.create(
            customer=self.customer,
            producer=self.producer,
        )
        product = Product.objects.create(
            producer=self.producer,
            category=self.category,
            name="Favourite Apples",
            description="Surplus apples",
            price=Decimal("2.80"),
            stock_quantity=6,
            is_surplus=True,
            surplus_discount_percent=30,
            surplus_stock_quantity=2,
            surplus_expires_at=timezone.now() + timedelta(days=1),
        )
        notify_favourite_customers_about_surplus(product)

        self.client.force_login(self.customer)
        response = self.client.post(
            reverse("marketplace:toggle_favourite_producer", args=[self.producer.id]),
            {"next": reverse("marketplace:product_list")},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            FavouriteProducer.objects.filter(
                customer=self.customer,
                producer=self.producer,
            ).exists()
        )
        self.assertFalse(
            SurplusDealNotification.objects.filter(
                customer=self.customer,
                producer=self.producer,
            ).exists()
        )

    def test_only_unread_favourite_notifications_drive_popup_context(self):
        FavouriteProducer.objects.create(
            customer=self.customer,
            producer=self.producer,
        )
        product = Product.objects.create(
            producer=self.producer,
            category=self.category,
            name="Purple Popup Plums",
            description="Surplus plums",
            price=Decimal("5.10"),
            stock_quantity=9,
            is_surplus=True,
            surplus_discount_percent=15,
            surplus_stock_quantity=3,
            surplus_expires_at=timezone.now() + timedelta(days=1),
        )
        notify_favourite_customers_about_surplus(product)

        self.client.force_login(self.customer)
        response = self.client.get(reverse("marketplace:product_list"))

        self.assertEqual(response.context["unread_favourite_surplus_notification_count"], 1)
        self.assertEqual(
            response.context["latest_favourite_surplus_notification"].product,
            product,
        )

        SurplusDealNotification.objects.filter(
            customer=self.customer,
            producer=self.producer,
            product=product,
        ).update(is_read=True)

        response = self.client.get(reverse("marketplace:product_list"))
        self.assertEqual(response.context["unread_favourite_surplus_notification_count"], 0)
        self.assertIsNone(response.context["latest_favourite_surplus_notification"])


class TC19SurplusImpactAnalyticsTests(TestCase):
    def setUp(self):
        self.password = "Str0ng!Pass99"
        self.producer = _create_user(
            "impact_producer",
            "impact_producer@example.com",
            self.password,
            Profile.Role.PRODUCER,
            business_name="Impact Farm",
            is_verified=True,
        )
        self.customer = _create_user(
            "impact_customer",
            "impact_customer@example.com",
            self.password,
            Profile.Role.CUSTOMER,
            contact_first_name="Impact",
            contact_last_name="Customer",
        )
        self.category = Category.objects.create(name="Analytics Produce")

    def test_payment_flow_records_saved_surplus_analytics(self):
        product = Product.objects.create(
            producer=self.producer,
            category=self.category,
            name="Surplus Tomatoes",
            description="Discounted surplus tomatoes",
            price=Decimal("5.00"),
            stock_quantity=8,
            estimated_unit_weight_kg=Decimal("1.25"),
            is_surplus=True,
            surplus_discount_percent=20,
            surplus_stock_quantity=3,
            surplus_expires_at=timezone.now() + timedelta(days=1),
        )

        self.client.force_login(self.customer)
        session = self.client.session
        delivery_date = (timezone.localdate() + timedelta(days=2)).isoformat()
        session["order"] = {
            "address": "123 Bristol Road",
            "date": delivery_date,
            "payment": "stripe",
            "subtotal": 8.00,
            "commission": 0.40,
            "total": 8.40,
            "producers": {
                self.producer.username: [{
                    "name": product.name,
                    "price": float(product.discounted_price),
                    "qty": 2,
                    "total": float(product.discounted_price * 2),
                    "lead_time": 2,
                    "id": product.id,
                }]
            },
        }
        session.save()

        response = self.client.post(
            reverse("marketplace:payment"),
            {
                "card_number": "4242424242424242",
                "expiry": "12/30",
                "cvc": "123",
            },
        )

        self.assertEqual(response.status_code, 200)
        analytics_record = SurplusAnalyticsRecord.objects.get(
            producer=self.producer,
            product=product,
            record_type=SurplusAnalyticsRecord.RecordType.SAVED,
        )
        self.assertEqual(analytics_record.quantity, 2)
        self.assertEqual(analytics_record.estimated_weight_kg, Decimal("2.50"))
        self.assertEqual(analytics_record.customer_saving, Decimal("2.00"))
        self.assertEqual(analytics_record.revenue, Decimal("8.00"))

        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 6)
        self.assertEqual(product.surplus_stock_quantity, 1)

    def test_expire_surplus_deals_records_unsold_analytics_once(self):
        product = Product.objects.create(
            producer=self.producer,
            category=self.category,
            name="Unsold Lettuce",
            description="Expired surplus lettuce",
            price=Decimal("3.00"),
            stock_quantity=5,
            estimated_unit_weight_kg=Decimal("0.75"),
            is_surplus=True,
            surplus_discount_percent=25,
            surplus_stock_quantity=4,
            surplus_expires_at=timezone.now() - timedelta(hours=1),
        )

        expire_surplus_deals()
        expire_surplus_deals()

        records = SurplusAnalyticsRecord.objects.filter(
            producer=self.producer,
            product=product,
            record_type=SurplusAnalyticsRecord.RecordType.UNSOLD,
        )
        self.assertEqual(records.count(), 1)
        self.assertEqual(records.first().quantity, 4)
        self.assertEqual(records.first().estimated_weight_kg, Decimal("3.00"))

        product.refresh_from_db()
        self.assertFalse(product.is_surplus)
        self.assertEqual(product.surplus_stock_quantity, 0)

    def test_producer_surplus_impact_view_shows_totals(self):
        order = Order.objects.create(
            customer=self.customer,
            delivery_address="1 Impact Street",
            delivery_postcode="BS1 1AA",
            special_instructions="",
            total_amount=Decimal("10.00"),
        )
        product = Product.objects.create(
            producer=self.producer,
            category=self.category,
            name="Impact Carrots",
            description="Carrots",
            price=Decimal("4.00"),
            stock_quantity=10,
            estimated_unit_weight_kg=Decimal("0.50"),
        )

        SurplusAnalyticsRecord.objects.create(
            producer=self.producer,
            product=product,
            order=order,
            record_type=SurplusAnalyticsRecord.RecordType.SAVED,
            quantity=3,
            estimated_weight_kg=Decimal("1.50"),
            customer_saving=Decimal("1.20"),
            revenue=Decimal("6.80"),
        )
        SurplusAnalyticsRecord.objects.create(
            producer=self.producer,
            product=product,
            record_type=SurplusAnalyticsRecord.RecordType.UNSOLD,
            quantity=2,
            estimated_weight_kg=Decimal("1.00"),
            customer_saving=Decimal("0.00"),
            revenue=Decimal("0.00"),
        )

        self.client.force_login(self.producer)
        response = self.client.get(reverse("marketplace:producer_surplus_impact"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Surplus Impact Analytics")
        self.assertContains(response, "Impact Carrots")
        self.assertContains(response, "1.50 kg")
        self.assertContains(response, "£1.20")
        self.assertContains(response, "£6.80")
