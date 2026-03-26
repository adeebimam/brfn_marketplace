# Generated manually – adds business_type field and updates RESTAURANT role label

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_add_community_restaurant_admin_roles"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="business_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("RESTAURANT", "Restaurant"),
                    ("CAFE", "Café"),
                    ("BISTRO", "Bistro"),
                    ("TAKEAWAY", "Takeaway"),
                    ("PUB", "Pub / Bar"),
                    ("OTHER", "Other"),
                ],
                help_text="Sub-type for Restaurant / Café accounts.",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="profile",
            name="role",
            field=models.CharField(
                choices=[
                    ("CUSTOMER", "Customer"),
                    ("PRODUCER", "Producer"),
                    ("COMMUNITY_GROUP", "Community Group"),
                    ("RESTAURANT", "Restaurant / Café"),
                    ("ADMIN", "Admin"),
                ],
                default="CUSTOMER",
                max_length=20,
            ),
        ),
    ]
