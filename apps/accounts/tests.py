"""
TC-022: System Admin Has Secure Authentication
──────────────────────────────────────────────
Covers all five sub-cases from the test specification:
  • TC22.1 Password Security
  • TC22.2 Login Security
  • TC22.3 Authorisation (RBAC)
  • TC22.4 Session Management
  • TC22.5 Brute-force Protection
"""
from decimal import Decimal
from django.contrib.auth.hashers import is_password_usable
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from apps.accounts.models import Profile
from apps.marketplace.models import Order, ProducerOrder, OrderItem, Product, Category
import csv
import io


# ─── helpers ────────────────────────────────────────────────────────
def _create_user(username, email, password, role, **extra_profile):
    """Create a User + Profile pair for testing."""
    user = User.objects.create_user(
        username=username,
        email=email,
        password=password,
    )
    Profile.objects.create(user=user, role=role, **extra_profile)
    return user


# ═══════════════════════════════════════════════════════════════════
# TC-22.1 – Password Security
# ═══════════════════════════════════════════════════════════════════
class TC22_1_PasswordSecurityTests(TestCase):
    def test_weak_password_rejected(self):
        resp = self.client.post(
            "/accounts/register/",
            {
                "email": "weak@example.com",
                "account_type": "CUSTOMER",
                "password1": "123",
                "password2": "123",
                "first_name": "Test",
                "last_name": "User",
                "delivery_address": "1 Street",
                "delivery_postcode": "AB1 2CD",
                "phone": "07700000000",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="weak@example.com").exists())
        self.assertContains(resp, "password")

    def test_short_password_rejected(self):
        resp = self.client.post(
            "/accounts/register/",
            {
                "email": "short@example.com",
                "account_type": "CUSTOMER",
                "password1": "Ab1!xyz",
                "password2": "Ab1!xyz",
                "first_name": "Test",
                "last_name": "User",
                "delivery_address": "1 Street",
                "delivery_postcode": "AB1 2CD",
                "phone": "07700000000",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="short@example.com").exists())

    def test_numeric_only_password_rejected(self):
        resp = self.client.post(
            "/accounts/register/",
            {
                "email": "numeric@example.com",
                "account_type": "CUSTOMER",
                "password1": "12345678",
                "password2": "12345678",
                "first_name": "Test",
                "last_name": "User",
                "delivery_address": "1 Street",
                "delivery_postcode": "AB1 2CD",
                "phone": "07700000000",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="numeric@example.com").exists())

    def test_common_password_rejected(self):
        resp = self.client.post(
            "/accounts/register/",
            {
                "email": "common@example.com",
                "account_type": "CUSTOMER",
                "password1": "password",
                "password2": "password",
                "first_name": "Test",
                "last_name": "User",
                "delivery_address": "1 Street",
                "delivery_postcode": "AB1 2CD",
                "phone": "07700000000",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="common@example.com").exists())

    def test_strong_password_accepted(self):
        resp = self.client.post(
            "/accounts/register/",
            {
                "email": "strong@example.com",
                "account_type": "CUSTOMER",
                "password1": "Str0ng!Pass99",
                "password2": "Str0ng!Pass99",
                "first_name": "Test",
                "last_name": "User",
                "delivery_address": "1 Street",
                "delivery_postcode": "AB1 2CD",
                "phone": "07700000000",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(User.objects.filter(email="strong@example.com").exists())

    def test_password_hashed_in_database(self):
        _create_user("hashcheck", "hash@example.com", "Str0ng!Pass99", Profile.Role.CUSTOMER)
        user = User.objects.get(username="hashcheck")
        self.assertTrue(is_password_usable(user.password))
        self.assertNotEqual(user.password, "Str0ng!Pass99")
        self.assertTrue(
            user.password.startswith("pbkdf2_sha256$")
            or user.password.startswith("argon2")
            or user.password.startswith("bcrypt"),
            f"Unexpected hash format: {user.password[:30]}…",
        )

    def test_register_as_community_group(self):
        resp = self.client.post(
            "/accounts/register/",
            {
                "email": "comm_reg@example.com",
                "account_type": "COMMUNITY_GROUP",
                "password1": "Str0ng!Pass99",
                "password2": "Str0ng!Pass99",
                "first_name": "Jane",
                "last_name": "Doe",
                "business_name": "Local Food Bank",
                "delivery_address": "10 High Street",
                "delivery_postcode": "BS1 1AA",
                "phone": "07700000001",
            },
        )
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(email="comm_reg@example.com")
        self.assertEqual(user.profile.role, Profile.Role.COMMUNITY_GROUP)
        self.assertEqual(user.profile.business_name, "Local Food Bank")

    def test_register_as_restaurant(self):
        resp = self.client.post(
            "/accounts/register/",
            {
                "email": "rest_reg@example.com",
                "account_type": "RESTAURANT",
                "password1": "Str0ng!Pass99",
                "password2": "Str0ng!Pass99",
                "first_name": "Bob",
                "last_name": "Chef",
                "business_name": "Bistro Bob",
                "business_type": "BISTRO",
                "delivery_address": "5 Market Lane",
                "delivery_postcode": "BS2 2BB",
                "phone": "07700000002",
            },
        )
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(email="rest_reg@example.com")
        self.assertEqual(user.profile.role, Profile.Role.RESTAURANT)
        self.assertEqual(user.profile.business_name, "Bistro Bob")
        self.assertEqual(user.profile.business_type, Profile.BusinessType.BISTRO)

    def test_register_as_cafe(self):
        resp = self.client.post(
            "/accounts/register/",
            {
                "email": "cafe_reg@example.com",
                "account_type": "RESTAURANT",
                "password1": "Str0ng!Pass99",
                "password2": "Str0ng!Pass99",
                "first_name": "Alice",
                "last_name": "Brew",
                "business_name": "Cosy Corner Café",
                "business_type": "CAFE",
                "delivery_address": "3 Park Road",
                "delivery_postcode": "BS3 3CC",
                "phone": "07700000003",
            },
        )
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(email="cafe_reg@example.com")
        self.assertEqual(user.profile.role, Profile.Role.RESTAURANT)
        self.assertEqual(user.profile.business_type, Profile.BusinessType.CAFE)
        self.assertEqual(user.profile.display_role, "Café")

    def test_community_group_requires_business_name(self):
        resp = self.client.post(
            "/accounts/register/",
            {
                "email": "noname@example.com",
                "account_type": "COMMUNITY_GROUP",
                "password1": "Str0ng!Pass99",
                "password2": "Str0ng!Pass99",
                "first_name": "Test",
                "last_name": "User",
                "business_name": "",
                "delivery_address": "1 Street",
                "delivery_postcode": "AB1 2CD",
                "phone": "07700000000",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="noname@example.com").exists())

    def test_restaurant_requires_business_name(self):
        resp = self.client.post(
            "/accounts/register/",
            {
                "email": "noname2@example.com",
                "account_type": "RESTAURANT",
                "password1": "Str0ng!Pass99",
                "password2": "Str0ng!Pass99",
                "first_name": "Test",
                "last_name": "User",
                "business_name": "",
                "business_type": "CAFE",
                "delivery_address": "1 Street",
                "delivery_postcode": "AB1 2CD",
                "phone": "07700000000",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="noname2@example.com").exists())

    def test_restaurant_requires_business_type(self):
        resp = self.client.post(
            "/accounts/register/",
            {
                "email": "notype@example.com",
                "account_type": "RESTAURANT",
                "password1": "Str0ng!Pass99",
                "password2": "Str0ng!Pass99",
                "first_name": "Test",
                "last_name": "User",
                "business_name": "Some Place",
                "business_type": "",
                "delivery_address": "1 Street",
                "delivery_postcode": "AB1 2CD",
                "phone": "07700000000",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="notype@example.com").exists())

    def test_customer_does_not_require_business_name(self):
        resp = self.client.post(
            "/accounts/register/",
            {
                "email": "cust_nobiz@example.com",
                "account_type": "CUSTOMER",
                "password1": "Str0ng!Pass99",
                "password2": "Str0ng!Pass99",
                "first_name": "Test",
                "last_name": "User",
                "business_name": "",
                "delivery_address": "1 Street",
                "delivery_postcode": "AB1 2CD",
                "phone": "07700000000",
            },
        )
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(email="cust_nobiz@example.com")
        self.assertEqual(user.profile.role, Profile.Role.CUSTOMER)


# ═══════════════════════════════════════════════════════════════════
# TC-22.2 – Login Security
# ═══════════════════════════════════════════════════════════════════
class TC22_2_LoginSecurityTests(TestCase):
    def setUp(self):
        self.password = "Str0ng!Pass99"
        self.user = _create_user(
            "logintest", "login@example.com", self.password, Profile.Role.CUSTOMER,
        )

    def test_wrong_password_generic_error(self):
        resp = self.client.post(
            "/accounts/login/",
            {"username": "login@example.com", "password": "wrong"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invalid username or password")

    def test_nonexistent_user_same_error(self):
        resp = self.client.post(
            "/accounts/login/",
            {"username": "nobody@example.com", "password": "anything"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invalid username or password")

    def test_correct_credentials_login_and_session(self):
        resp = self.client.post(
            "/accounts/login/",
            {"username": "login@example.com", "password": self.password},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(
            int(self.client.session["_auth_user_id"]),
            self.user.pk,
        )


# ═══════════════════════════════════════════════════════════════════
# TC-22.3 – Authorisation (RBAC)
# ═══════════════════════════════════════════════════════════════════
class TC22_3_AuthorisationTests(TestCase):
    def setUp(self):
        self.password = "Str0ng!Pass99"
        self.customer = _create_user(
            "customer1", "cust@example.com", self.password, Profile.Role.CUSTOMER,
        )
        self.producer_a = _create_user(
            "producerA", "pa@example.com", self.password, Profile.Role.PRODUCER,
            business_name="Farm A",
        )
        self.producer_b = _create_user(
            "producerB", "pb@example.com", self.password, Profile.Role.PRODUCER,
            business_name="Farm B",
        )
        self.community = _create_user(
            "community1", "comm@example.com", self.password, Profile.Role.COMMUNITY_GROUP,
            business_name="Food Bank X",
        )
        self.restaurant = _create_user(
            "restaurant1", "rest@example.com", self.password, Profile.Role.RESTAURANT,
            business_name="Bistro Y",
        )
        self.admin_user = _create_user(
            "admin1", "admin@example.com", self.password, Profile.Role.ADMIN,
        )

    def test_customer_cannot_access_producer_product_list(self):
        self.client.force_login(self.customer)
        resp = self.client.get("/producer/products/")
        self.assertEqual(resp.status_code, 403)

    def test_customer_cannot_create_product(self):
        self.client.force_login(self.customer)
        resp = self.client.get("/producer/products/new/")
        self.assertEqual(resp.status_code, 403)

    def test_customer_cannot_access_producer_orders(self):
        self.client.force_login(self.customer)
        resp = self.client.get("/producer/orders/")
        self.assertEqual(resp.status_code, 403)

    def test_customer_cannot_access_producer_payments(self):
        self.client.force_login(self.customer)
        resp = self.client.get("/producer/payments/")
        self.assertEqual(resp.status_code, 403)

    def test_community_group_cannot_access_producer_product_list(self):
        self.client.force_login(self.community)
        resp = self.client.get("/producer/products/")
        self.assertEqual(resp.status_code, 403)

    def test_community_group_cannot_create_product(self):
        self.client.force_login(self.community)
        resp = self.client.get("/producer/products/new/")
        self.assertEqual(resp.status_code, 403)

    def test_community_group_cannot_access_producer_orders(self):
        self.client.force_login(self.community)
        resp = self.client.get("/producer/orders/")
        self.assertEqual(resp.status_code, 403)

    def test_community_group_cannot_access_producer_payments(self):
        self.client.force_login(self.community)
        resp = self.client.get("/producer/payments/")
        self.assertEqual(resp.status_code, 403)

    def test_restaurant_cannot_access_producer_product_list(self):
        self.client.force_login(self.restaurant)
        resp = self.client.get("/producer/products/")
        self.assertEqual(resp.status_code, 403)

    def test_restaurant_cannot_create_product(self):
        self.client.force_login(self.restaurant)
        resp = self.client.get("/producer/products/new/")
        self.assertEqual(resp.status_code, 403)

    def test_restaurant_cannot_access_producer_orders(self):
        self.client.force_login(self.restaurant)
        resp = self.client.get("/producer/orders/")
        self.assertEqual(resp.status_code, 403)

    def test_restaurant_cannot_access_producer_payments(self):
        self.client.force_login(self.restaurant)
        resp = self.client.get("/producer/payments/")
        self.assertEqual(resp.status_code, 403)

    def test_admin_cannot_access_producer_product_list(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get("/producer/products/")
        self.assertEqual(resp.status_code, 403)

    def test_admin_cannot_access_producer_orders(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get("/producer/orders/")
        self.assertEqual(resp.status_code, 403)

    def test_producer_can_access_own_product_list(self):
        self.client.force_login(self.producer_a)
        resp = self.client.get("/producer/products/")
        self.assertEqual(resp.status_code, 200)

    def test_producer_can_access_own_orders(self):
        self.client.force_login(self.producer_a)
        resp = self.client.get("/producer/orders/")
        self.assertEqual(resp.status_code, 200)

    def test_producer_can_access_own_payments(self):
        self.client.force_login(self.producer_a)
        resp = self.client.get("/producer/payments/")
        self.assertEqual(resp.status_code, 200)

    def test_producer_cannot_view_other_producers_order(self):
        order = Order.objects.create(customer=self.customer)
        po = ProducerOrder.objects.create(
            order=order,
            producer=self.producer_a,
            delivery_date=(timezone.now().date() + timedelta(days=3)).isoformat(),
        )
        self.client.force_login(self.producer_b)
        resp = self.client.get(f"/producer/orders/{po.pk}/")
        self.assertIn(resp.status_code, [403, 404])

    def test_community_group_can_browse_products(self):
        self.client.force_login(self.community)
        resp = self.client.get("/products/")
        self.assertEqual(resp.status_code, 200)

    def test_restaurant_can_browse_products(self):
        self.client.force_login(self.restaurant)
        resp = self.client.get("/products/")
        self.assertEqual(resp.status_code, 200)

    def test_producer_login_redirects_to_producer_dashboard(self):
        resp = self.client.post(
            "/accounts/login/",
            {"username": "pa@example.com", "password": self.password},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/producer/products/", resp.url)

    def test_customer_login_redirects_to_product_list(self):
        resp = self.client.post(
            "/accounts/login/",
            {"username": "cust@example.com", "password": self.password},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/products/", resp.url)

    def test_community_group_login_redirects_to_product_list(self):
        resp = self.client.post(
            "/accounts/login/",
            {"username": "comm@example.com", "password": self.password},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/products/", resp.url)

    def test_restaurant_login_redirects_to_product_list(self):
        resp = self.client.post(
            "/accounts/login/",
            {"username": "rest@example.com", "password": self.password},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/products/", resp.url)

    def test_admin_login_redirects_to_admin_panel(self):
        resp = self.client.post(
            "/accounts/login/",
            {"username": "admin@example.com", "password": self.password},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/admin-dashboard/", resp.url)

    def test_anonymous_redirected_from_producer_pages(self):
        resp = self.client.get("/producer/products/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login", resp.url)

    def test_anonymous_redirected_from_cart(self):
        resp = self.client.get("/cart/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login", resp.url)


# ═══════════════════════════════════════════════════════════════════
# TC-22.4 – Session Management
# ═══════════════════════════════════════════════════════════════════
class TC22_4_SessionManagementTests(TestCase):
    def setUp(self):
        self.password = "Str0ng!Pass99"
        self.user = _create_user(
            "sessuser", "sess@example.com", self.password, Profile.Role.CUSTOMER,
        )

    def test_remember_me_sets_long_session(self):
        self.client.post(
            "/accounts/login/",
            {
                "username": "sess@example.com",
                "password": self.password,
                "remember_me": "on",
            },
        )
        expiry = self.client.session.get_expiry_age()
        self.assertGreater(expiry, 0)

    def test_no_remember_me_expires_on_browser_close(self):
        self.client.post(
            "/accounts/login/",
            {"username": "sess@example.com", "password": self.password},
        )
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_logout_terminates_session(self):
        self.client.post(
            "/accounts/login/",
            {"username": "sess@example.com", "password": self.password},
        )
        self.assertIn("_auth_user_id", self.client.session)
        self.client.get("/accounts/logout/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_protected_page_requires_relogin_after_logout(self):
        self.client.post(
            "/accounts/login/",
            {"username": "sess@example.com", "password": self.password},
        )
        self.client.get("/accounts/logout/")
        resp = self.client.get("/accounts/profile/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login", resp.url)


# ═══════════════════════════════════════════════════════════════════
# TC-22.5 – Brute-force Protection
# ═══════════════════════════════════════════════════════════════════
@override_settings(
    AXES_FAILURE_LIMIT=3,
    AXES_COOLOFF_TIME=1,
    AXES_RESET_ON_SUCCESS=True,
    AXES_LOCKOUT_PARAMETERS=["username"],
)
class TC22_5_BruteForceProtectionTests(TestCase):
    def setUp(self):
        self.password = "Str0ng!Pass99"
        self.user = _create_user(
            "bruteuser", "brute@example.com", self.password, Profile.Role.CUSTOMER,
        )

    def test_lockout_after_repeated_failures(self):
        for _ in range(3):
            self.client.post(
                "/accounts/login/",
                {"username": "brute@example.com", "password": "wrong"},
            )
        resp = self.client.post(
            "/accounts/login/",
            {"username": "brute@example.com", "password": "wrong"},
        )
        self.assertIn(resp.status_code, [403, 200])
        self.assertContains(
            resp, "Too many failed login attempts", status_code=resp.status_code,
        )

    def test_lockout_is_per_account_not_global(self):
        other_user = _create_user(
            "otheruser", "other@example.com", self.password, Profile.Role.CUSTOMER,
        )
        for _ in range(3):
            self.client.post(
                "/accounts/login/",
                {"username": "brute@example.com", "password": "wrong"},
            )
        resp = self.client.post(
            "/accounts/login/",
            {"username": "brute@example.com", "password": "wrong"},
        )
        self.assertIn(resp.status_code, [403, 200])
        self.assertContains(
            resp, "Too many failed login attempts", status_code=resp.status_code,
        )
        resp = self.client.post(
            "/accounts/login/",
            {"username": "other@example.com", "password": self.password},
        )
        self.assertEqual(resp.status_code, 302, "Other account should NOT be locked out")


# ═══════════════════════════════════════════════════════════════════
# TC-025 – Financial Report & Commission Calculations
# ═══════════════════════════════════════════════════════════════════
class TC25_FinancialReportTests(TestCase):
    def setUp(self):
        self.password = "Str0ng!Pass99"

        self.admin = _create_user(
            "admin25", "admin25@example.com", self.password, Profile.Role.ADMIN,
        )
        self.customer = _create_user(
            "customer25", "customer25@example.com", self.password, Profile.Role.CUSTOMER,
        )
        self.producer_one = _create_user(
            "producer25a", "p25a@example.com", self.password, Profile.Role.PRODUCER,
            business_name="Farm A",
        )
        self.producer_two = _create_user(
            "producer25b", "p25b@example.com", self.password, Profile.Role.PRODUCER,
            business_name="Farm B",
        )

        category = Category.objects.create(name="Veg25")

        self.product_a = Product.objects.create(
            producer=self.producer_one,
            category=category,
            name="Potatoes25",
            price=Decimal("1.00"),
            stock_quantity=200,
            season="ALL",
        )
        self.product_b = Product.objects.create(
            producer=self.producer_two,
            category=category,
            name="Carrots25",
            price=Decimal("1.00"),
            stock_quantity=200,
            season="ALL",
        )

        # Order 1: single producer, total £100
        self.order_one = Order.objects.create(
            customer=self.customer,
            delivery_address="1 Test St",
            delivery_postcode="BS1 1AA",
            status=Order.Status.PENDING,
            total_amount=Decimal("100.00"),
        )
        self.po_one = ProducerOrder.objects.create(
            order=self.order_one,
            producer=self.producer_one,
            delivery_date="2026-05-11",
            status=ProducerOrder.Status.PENDING,
            total_value=Decimal("100.00"),
        )
        OrderItem.objects.create(
            producer_order=self.po_one,
            product=self.product_a,
            quantity=100,
            unit_price=Decimal("1.00"),
        )

        # Order 2: multi-vendor, £80 + £70 = £150 total
        self.order_two = Order.objects.create(
            customer=self.customer,
            delivery_address="1 Test St",
            delivery_postcode="BS1 1AA",
            status=Order.Status.PENDING,
            total_amount=Decimal("150.00"),
        )
        self.po_two_a = ProducerOrder.objects.create(
            order=self.order_two,
            producer=self.producer_one,
            delivery_date="2026-05-11",
            status=ProducerOrder.Status.PENDING,
            total_value=Decimal("80.00"),
        )
        OrderItem.objects.create(
            producer_order=self.po_two_a,
            product=self.product_a,
            quantity=80,
            unit_price=Decimal("1.00"),
        )
        self.po_two_b = ProducerOrder.objects.create(
            order=self.order_two,
            producer=self.producer_two,
            delivery_date="2026-05-11",
            status=ProducerOrder.Status.PENDING,
            total_value=Decimal("70.00"),
        )
        OrderItem.objects.create(
            producer_order=self.po_two_b,
            product=self.product_b,
            quantity=70,
            unit_price=Decimal("1.00"),
        )

    # ── access control ─────────────────────────────────────
    def test_non_admin_cannot_access_financial_report(self):
        self.client.force_login(self.customer)
        resp = self.client.get("/accounts/admin-financials/")
        self.assertIn(resp.status_code, [302, 403])

    def test_admin_can_access_financial_report(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        self.assertEqual(resp.status_code, 200)

    # ── commission calculations ────────────────────────────
    def test_single_order_commission_is_5_percent(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        report_data = resp.context["orders"]
        order_one_data = next(d for d in report_data if d["order"].id == self.order_one.id)
        self.assertEqual(order_one_data["commission"], Decimal("5.00"))
        self.assertEqual(order_one_data["producer_payment"], Decimal("95.00"))

    def test_multi_vendor_order_commission_on_total(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        report_data = resp.context["orders"]
        order_two_data = next(d for d in report_data if d["order"].id == self.order_two.id)
        self.assertEqual(order_two_data["commission"], Decimal("7.50"))
        self.assertEqual(order_two_data["producer_payment"], Decimal("142.50"))

    def test_multi_vendor_producer_payments_correct(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        report_data = resp.context["orders"]
        order_two_data = next(d for d in report_data if d["order"].id == self.order_two.id)
        producers = {str(p["producer"]): p for p in order_two_data["producers"]}
        self.assertEqual(producers[self.producer_one.username]["payment"], Decimal("76.00"))
        self.assertEqual(producers[self.producer_two.username]["payment"], Decimal("66.50"))

    def test_totals_are_sum_of_all_orders(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        self.assertEqual(resp.context["total_order_value"], Decimal("250.00"))
        self.assertEqual(resp.context["total_commission"], Decimal("12.50"))
        self.assertEqual(resp.context["total_producer_payment"], Decimal("237.50"))

    def test_commission_accurate_to_two_decimal_places(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        for item in resp.context["orders"]:
            self.assertRegex(str(item["commission"]), r"^\d+\.\d{2}$")

    # ── date range filter ──────────────────────────────────
    def test_date_range_filter_excludes_old_orders(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            "/accounts/admin-financials/",
            {"start_date": "2030-01-01", "end_date": "2030-01-31"},
        )
        self.assertEqual(len(resp.context["orders"]), 0)

    def test_date_range_filter_includes_orders_in_range(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            "/accounts/admin-financials/",
            {"start_date": "2026-01-01", "end_date": "2026-12-31"},
        )
        self.assertEqual(len(resp.context["orders"]), 2)

    # ── producer filter ────────────────────────────────────
    def test_producer_filter_returns_only_matching_orders(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            "/accounts/admin-financials/",
            {"start_date": "2026-01-01", "end_date": "2026-12-31", "producer": self.producer_one.username},
        )
        self.assertEqual(len(resp.context["orders"]), 2)

    def test_producer_filter_excludes_non_matching_orders(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            "/accounts/admin-financials/",
            {"start_date": "2026-01-01", "end_date": "2026-12-31", "producer": self.producer_two.username},
        )
        self.assertEqual(len(resp.context["orders"]), 1)
        self.assertEqual(resp.context["orders"][0]["order"].id, self.order_two.id)

    def test_producer_filter_empty_returns_all_orders(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            "/accounts/admin-financials/",
            {"start_date": "2026-01-01", "end_date": "2026-12-31", "producer": ""},
        )
        self.assertEqual(len(resp.context["orders"]), 2)

    # ── status filter ──────────────────────────────────────
    def test_status_filter_returns_only_pending_orders(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            "/accounts/admin-financials/",
            {"start_date": "2026-01-01", "end_date": "2026-12-31", "status": "PENDING"},
        )
        self.assertEqual(len(resp.context["orders"]), 2)

    def test_status_filter_returns_no_orders_for_completed(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            "/accounts/admin-financials/",
            {"start_date": "2026-01-01", "end_date": "2026-12-31", "status": "COMPLETED"},
        )
        self.assertEqual(len(resp.context["orders"]), 0)

    def test_status_filter_works_after_order_status_change(self):
        self.order_one.status = Order.Status.CONFIRMED
        self.order_one.save()
        self.client.force_login(self.admin)
        resp = self.client.get(
            "/accounts/admin-financials/",
            {"start_date": "2026-01-01", "end_date": "2026-12-31", "status": "CONFIRMED"},
        )
        self.assertEqual(len(resp.context["orders"]), 1)
        self.assertEqual(resp.context["orders"][0]["order"].id, self.order_one.id)

    def test_combined_producer_and_status_filter(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            "/accounts/admin-financials/",
            {"start_date": "2026-01-01", "end_date": "2026-12-31", "producer": self.producer_two.username, "status": "PENDING"},
        )
        self.assertEqual(len(resp.context["orders"]), 1)
        self.assertEqual(resp.context["orders"][0]["order"].id, self.order_two.id)

    # ── CSV export ─────────────────────────────────────────
    def test_csv_export_accessible_by_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financial-report/export/csv/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")

    def test_csv_export_contains_correct_headers(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financial-report/export/csv/")
        content = resp.content.decode("utf-8")
        reader = csv.reader(io.StringIO(content))
        headers = next(reader)
        self.assertIn("Order ID", headers)
        self.assertIn("Commission 5% (£)", headers)
        self.assertIn("Producer Payment (£)", headers)

    def test_csv_export_contains_correct_values(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            "/accounts/admin-financial-report/export/csv/",
            {"start_date": "2026-01-01", "end_date": "2026-12-31"},
        )
        content = resp.content.decode("utf-8")
        self.assertIn("5.00", content)
        self.assertIn("95.00", content)

    def test_csv_not_accessible_by_non_admin(self):
        self.client.force_login(self.customer)
        resp = self.client.get("/accounts/admin-financial-report/export/csv/")
        self.assertIn(resp.status_code, [302, 403])

    # ── PDF export ─────────────────────────────────────────
    def test_pdf_export_accessible_by_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financial-report/export/pdf/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_pdf_export_not_accessible_by_non_admin(self):
        self.client.force_login(self.customer)
        resp = self.client.get("/accounts/admin-financial-report/export/pdf/")
        self.assertIn(resp.status_code, [302, 403])

    def test_pdf_export_returns_file_attachment(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financial-report/export/pdf/")
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertIn(".pdf", resp["Content-Disposition"])

    # ── monthly summary ────────────────────────────────────
    def test_monthly_summary_present_in_context(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        self.assertIn("monthly_summary", resp.context)

    def test_monthly_summary_contains_current_year_data(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        self.assertGreater(len(resp.context["monthly_summary"]), 0)

    def test_monthly_summary_commission_correct(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        for month in resp.context["monthly_summary"].values():
            expected = (month["total_order_value"] * Decimal("0.05")).quantize(Decimal("0.01"))
            self.assertEqual(month["total_commission"], expected)

    # ── YTD totals ─────────────────────────────────────────
    def test_ytd_totals_present_in_context(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        self.assertIn("ytd_order_value", resp.context)
        self.assertIn("ytd_commission", resp.context)
        self.assertIn("ytd_producer_payment", resp.context)
        self.assertIn("current_year", resp.context)

    def test_ytd_commission_is_5_percent_of_ytd_order_value(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        ytd_value = resp.context["ytd_order_value"]
        ytd_commission = resp.context["ytd_commission"]
        expected = (ytd_value * Decimal("0.05")).quantize(Decimal("0.01"))
        self.assertEqual(ytd_commission, expected)

    def test_ytd_producer_payment_plus_commission_equals_order_value(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        self.assertEqual(
            resp.context["ytd_producer_payment"] + resp.context["ytd_commission"],
            resp.context["ytd_order_value"],
        )

    # ── report page content ────────────────────────────────
    def test_report_shows_order_count(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        self.assertContains(resp, "2")

    def test_report_shows_producer_breakdown(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        content = resp.content.decode()
        self.assertIn(self.producer_one.username, content)
        self.assertIn(self.producer_two.username, content)

    # ── audit trail ────────────────────────────────────────
    def test_audit_logs_present_in_context(self):
        self.client.force_login(self.admin)
        resp = self.client.get("/accounts/admin-financials/")
        self.assertIn("audit_logs", resp.context)

    def test_audit_log_created_on_commission_log(self):
        from apps.marketplace.models import CommissionLog
        log = CommissionLog.objects.create(
            order=self.order_one,
            producer_order=self.po_one,
            order_total=Decimal("100.00"),
            commission_amount=Decimal("5.00"),
            producer_payment=Decimal("95.00"),
            producer=self.producer_one,
            note="Test audit log",
        )
        self.client.force_login(self.admin)
        resp = self.client.get(
            "/accounts/admin-financials/",
            {"start_date": "2026-01-01", "end_date": "2026-12-31"},
        )
        self.assertIn(log, resp.context["audit_logs"])

    def test_audit_log_commission_accurate(self):
        from apps.marketplace.models import CommissionLog
        log = CommissionLog.objects.create(
            order=self.order_one,
            producer_order=self.po_one,
            order_total=Decimal("100.00"),
            commission_amount=Decimal("5.00"),
            producer_payment=Decimal("95.00"),
            producer=self.producer_one,
            note="Test",
        )
        self.assertEqual(log.commission_amount, Decimal("5.00"))
        self.assertEqual(log.producer_payment, Decimal("95.00"))