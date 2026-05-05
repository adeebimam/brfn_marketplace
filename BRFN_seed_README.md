# BRFN Django seed data

This fixture is designed for your current Django apps:

- `apps.accounts`
- `apps.marketplace`
- `apps.cart`

It seeds the following models:

- `auth.User`
- `accounts.Profile`
- `marketplace.Category`
- `marketplace.Allergen`
- `marketplace.Product`
- `cart.Cart`
- `cart.CartItem`
- `marketplace.Order`
- `marketplace.ProducerOrder`
- `marketplace.OrderItem`
- `marketplace.ProducerOrderStatusHistory`

## Database name

Your Django settings use this database name by default:

```python
DB_NAME=brfn_db
```

## What is included

- 8 users
  - 4 producers
  - 3 customers
  - 1 admin/staff user
- 7 profiles
- 5 categories
- all 14 UK-recognised allergens
- 18 products with a mix of:
  - different categories
  - different seasons (`SPRING`, `SUMMER`, `AUTUMN`, `WINTER`, `ALL`)
  - linked allergens
  - `other_allergen_info`
  - active and inactive products
- 3 carts with sample items
- 4 customer orders
- 6 producer orders
- 11 order items
- 8 producer order status history records

## Shared passwords

All normal users:
- password: `Password123!`

Admin user:
- username: `brfn_admin`
- password: `Admin123!`

## Seed users (emails & logins by role)

Use the usernames below to sign in. Normal users use the shared password `Password123!` (see above). The admin user uses `Admin123!`.

- Producers
  - jane_smith — jane.smith@bristolvalleyfarm.com
  - hillside_dairy — orders@hillsidedairy.co.uk
  - avon_bakery — contact@avonbakery.co.uk
  - severn_orchards — hello@severnorchards.co.uk

- Customers
  - robert_johnson — robert.johnson@email.com
  - sarah_williams — sarah.williams@email.com
  - clifton_school — catering@cliftonschool.org.uk

- Admin / staff
  - brfn_admin — admin@brfn.local

## Import steps

Run this from your Django project root:

```bash
python manage.py migrate
python manage.py loaddata brfn_seed.json
```

If your fixture is in another folder, use the correct path, for example:

```bash
python manage.py loaddata /path/to/brfn_seed.json
```

## If you need to reset and reload

```bash
python manage.py flush
python manage.py loaddata brfn_seed.json
```

## Notes

- This fixture assumes you are using Django's default `auth.User`.
- It also assumes your app labels are:
  - `accounts`
  - `marketplace`
  - `cart`
- Because `Product.season` is a choice field, seasons are stored directly on products and **not** in a separate `Season` table.
- The 14 allergens are seeded as selectable rows in `Allergen`. Only some are linked to the test products on purpose, so your team can test products with zero, one, or multiple allergens.
- The password hashes were generated in Django-compatible PBKDF2 format. If your project uses a non-standard password hasher setup, the passwords may need to be reset locally.
