"""Merge migration to resolve conflicting 0012 leaf nodes.

This migration depends on both 0012 variants and creates a single unified
leaf for the migration graph. It has no operations.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0012_alter_product_harvest_date'),
        ('marketplace', '0012_drop_product_allergens_column'),
    ]

    operations = [
    ]
