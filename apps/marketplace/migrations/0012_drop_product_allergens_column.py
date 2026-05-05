from django.db import migrations


def drop_legacy_allergens(apps, schema_editor):
    """Drop the legacy 'allergens' column if it exists (MySQL-safe check).

    We query information_schema to avoid relying on `ALTER TABLE ... DROP COLUMN IF EXISTS`.
    """
    conn = schema_editor.connection
    if conn.vendor != "mysql":
        return

    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s
            """,
            ["marketplace_product", "allergens"],
        )
        result = cursor.fetchone()
        if result and result[0] > 0:
            cursor.execute("ALTER TABLE marketplace_product DROP COLUMN allergens")


class Migration(migrations.Migration):

    dependencies = [
        ("marketplace", "0011_normalize_harvest_date"),
    ]

    operations = [
        migrations.RunPython(drop_legacy_allergens, reverse_code=migrations.RunPython.noop),
    ]
