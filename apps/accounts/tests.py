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
from django.contrib.auth.hashers import is_password_usable
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from apps.accounts.models import Profile


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
    """
    • Weak passwords ('123') are rejected with requirement hints
    • Strong passwords are accepted
    • Passwords are stored hashed (not plain text)
    """

    def test_weak_password_rejected(self):
        """Register with '123' → system rejects and shows requirements."""
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
        # Should NOT redirect (form invalid → re-rendered)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="weak@example.com").exists())
        # Password-error text visible in response
        self.assertContains(resp, "password")

    def test_short_password_rejected(self):
        """Password under 8 characters must be rejected."""
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
        """Entirely numeric passwords must be rejected."""
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
        """Common passwords (e.g. 'password') must be rejected."""
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
        """A strong password meeting all requirements must succeed."""
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
        # Successful registration → redirect to login
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(User.objects.filter(email="strong@example.com").exists())

    def test_password_hashed_in_database(self):
        """Stored password must be hashed, never stored as plain text."""
        _create_user("hashcheck", "hash@example.com", "Str0ng!Pass99", Profile.Role.CUSTOMER)
        user = User.objects.get(username="hashcheck")
        # Django marks hashed passwords as "usable"
        self.assertTrue(is_password_usable(user.password))
        # Must NOT be the raw password
        self.assertNotEqual(user.password, "Str0ng!Pass99")
        # Should start with algorithm identifier (pbkdf2_sha256, argon2, etc.)
        self.assertTrue(
            user.password.startswith("pbkdf2_sha256$")
            or user.password.startswith("argon2")
            or user.password.startswith("bcrypt"),
            f"Unexpected hash format: {user.password[:30]}…",
        )

    def test_register_as_community_group(self):
        """Community Group registration with business name succeeds."""
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
        """Restaurant registration with business name and type succeeds."""
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
        """Café registration stores the correct business_type."""
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
        # display_role should show "Café", not "Restaurant / Café"
        self.assertEqual(user.profile.display_role, "Café")

    def test_community_group_requires_business_name(self):
        """Community Group without business name must be rejected."""
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
        """Restaurant without business name must be rejected."""
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
        """Restaurant without business type must be rejected."""
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
        """Customer registration without business name should succeed."""
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
    """
    • Wrong password → generic error (no user-existence leak)
    • Non-existent user → same generic error
    • Correct credentials → session created
    """

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
        """Error message must not reveal whether the user exists."""
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
        # Should redirect on success
        self.assertEqual(resp.status_code, 302)
        # Session should contain the authenticated user
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(
            int(self.client.session["_auth_user_id"]),
            self.user.pk,
        )


# ═══════════════════════════════════════════════════════════════════
# TC-22.3 – Authorisation (RBAC) – All 5 roles
# ═══════════════════════════════════════════════════════════════════
class TC22_3_AuthorisationTests(TestCase):
    """
    RBAC coverage for all five roles:
      CUSTOMER, PRODUCER, COMMUNITY_GROUP, RESTAURANT, ADMIN

    • Customers / Community Groups / Restaurants cannot access producer features
    • Producers can access their own producer features
    • Producers cannot access another producer's order details
    • Admin role redirects to /admin/ on login
    • Community Group and Restaurant behave like customers for browsing
    • Anonymous users are redirected to login
    """

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

    # ── customer blocked from producer pages ───────────────
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

    # ── community group blocked from producer pages ────────
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

    # ── restaurant blocked from producer pages ─────────────
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

    # ── admin blocked from producer pages (not a producer) ─
    def test_admin_cannot_access_producer_product_list(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get("/producer/products/")
        self.assertEqual(resp.status_code, 403)

    def test_admin_cannot_access_producer_orders(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get("/producer/orders/")
        self.assertEqual(resp.status_code, 403)

    # ── producer CAN access own pages ──────────────────────
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

    # ── producer cannot see other producer's order detail ──
    def test_producer_cannot_view_other_producers_order(self):
        """Producer B must not see Producer A's order details."""
        from apps.marketplace.models import Order, ProducerOrder

        order = Order.objects.create(customer=self.customer)
        po = ProducerOrder.objects.create(
            order=order,
            producer=self.producer_a,
            delivery_date="2026-04-01",
        )
        # Log in as producer B
        self.client.force_login(self.producer_b)
        resp = self.client.get(f"/producer/orders/{po.pk}/")
        self.assertIn(resp.status_code, [403, 404])

    # ── community group and restaurant can browse products ─
    def test_community_group_can_browse_products(self):
        self.client.force_login(self.community)
        resp = self.client.get("/products/")
        self.assertEqual(resp.status_code, 200)

    def test_restaurant_can_browse_products(self):
        self.client.force_login(self.restaurant)
        resp = self.client.get("/products/")
        self.assertEqual(resp.status_code, 200)

    # ── role-based login redirects ─────────────────────────
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
        self.assertIn("/admin/", resp.url)

    # ── anonymous users redirected to login ────────────────
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
    """
    • "Remember me" sets a long-lived session
    • Without "remember me" session expires on browser close
    • Logout terminates session, protected pages require re-login
    """

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
        # Session expiry should be > 0 (long-lived, not browser-close)
        expiry = self.client.session.get_expiry_age()
        self.assertGreater(expiry, 0)

    def test_no_remember_me_expires_on_browser_close(self):
        self.client.post(
            "/accounts/login/",
            {"username": "sess@example.com", "password": self.password},
        )
        # get_expire_at_browser_close() should be True
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_logout_terminates_session(self):
        # Log in
        self.client.post(
            "/accounts/login/",
            {"username": "sess@example.com", "password": self.password},
        )
        self.assertIn("_auth_user_id", self.client.session)

        # Log out
        self.client.get("/accounts/logout/")

        # Session should no longer contain the user
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_protected_page_requires_relogin_after_logout(self):
        # Log in, then out
        self.client.post(
            "/accounts/login/",
            {"username": "sess@example.com", "password": self.password},
        )
        self.client.get("/accounts/logout/")

        # Accessing a protected page should redirect to login
        resp = self.client.get("/accounts/profile/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login", resp.url)


# ═══════════════════════════════════════════════════════════════════
# TC-22.5 – Brute-force / rate-limiting (django-axes)
# ═══════════════════════════════════════════════════════════════════
@override_settings(
    AXES_FAILURE_LIMIT=3,
    AXES_COOLOFF_TIME=1,
    AXES_RESET_ON_SUCCESS=True,
    AXES_LOCKOUT_PARAMETERS=["username"],
)
class TC22_5_BruteForceProtectionTests(TestCase):
    """
    Verifies that django-axes locks an account after repeated failures.
    """

    def setUp(self):
        self.password = "Str0ng!Pass99"
        self.user = _create_user(
            "bruteuser", "brute@example.com", self.password, Profile.Role.CUSTOMER,
        )

    def test_lockout_after_repeated_failures(self):
        """After AXES_FAILURE_LIMIT bad attempts, further attempts are blocked."""
        for _ in range(3):
            self.client.post(
                "/accounts/login/",
                {"username": "brute@example.com", "password": "wrong"},
            )

        # Next attempt should be blocked (403 lockout)
        resp = self.client.post(
            "/accounts/login/",
            {"username": "brute@example.com", "password": "wrong"},
        )
        self.assertIn(resp.status_code, [403, 200])
        # Should show lockout message
        self.assertContains(
            resp, "Too many failed login attempts", status_code=resp.status_code,
        )

    def test_lockout_is_per_account_not_global(self):
        """Locking one account must NOT prevent other accounts from logging in."""
        # Create a second user
        other_user = _create_user(
            "otheruser", "other@example.com", self.password, Profile.Role.CUSTOMER,
        )

        # Lock out brute@example.com with 3 bad attempts
        for _ in range(3):
            self.client.post(
                "/accounts/login/",
                {"username": "brute@example.com", "password": "wrong"},
            )

        # Verify brute@example.com IS locked
        resp = self.client.post(
            "/accounts/login/",
            {"username": "brute@example.com", "password": "wrong"},
        )
        self.assertIn(resp.status_code, [403, 200])
        self.assertContains(
            resp, "Too many failed login attempts", status_code=resp.status_code,
        )

        # other@example.com should STILL be able to log in
        resp = self.client.post(
            "/accounts/login/",
            {"username": "other@example.com", "password": self.password},
        )
        self.assertEqual(resp.status_code, 302, "Other account should NOT be locked out")