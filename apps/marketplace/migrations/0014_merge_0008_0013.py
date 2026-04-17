"""Merge migration to unify two separate leaf nodes.

This migration depends on both the seasonal months migration (0008)
and the prior merge (0013) so the migration graph has a single leaf.
No operations are required.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0008_add_seasonal_months'),
        ('marketplace', '0013_merge_0012_conflicts'),
    ]

    operations = [
    ]
