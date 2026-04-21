from django.db import migrations


def normalize_harvest_dates(apps, schema_editor):
    Product = apps.get_model('marketplace', 'Product')
    # Iterate and convert datetime values to date-only (if present)
    for p in Product.objects.exclude(harvest_date__isnull=True):
        hd = p.harvest_date
        try:
            # If it's a datetime, take the date portion
            new_date = hd.date()
        except Exception:
            # If it's already a date or something unexpected, leave it
            new_date = hd
        p.harvest_date = new_date
        p.save(update_fields=['harvest_date'])


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0010_alter_product_harvest_date_alter_product_unit'),
    ]

    operations = [
        migrations.RunPython(normalize_harvest_dates, reverse_code=migrations.RunPython.noop),
    ]
