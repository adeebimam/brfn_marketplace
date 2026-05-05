from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("marketplace", "0019_order_total_amount"),
        ("marketplace", "0020_backfill_surplus_discount_amount"),
    ]

    operations = []
