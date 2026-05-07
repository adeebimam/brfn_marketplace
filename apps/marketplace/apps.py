from django.apps import AppConfig


class MarketplaceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.marketplace"

    def ready(self):
        import sys
        if 'migrate' in sys.argv or 'makemigrations' in sys.argv:
            return
        try:
            from django.db import connection
            tables = connection.introspection.table_names()
            if 'django_apscheduler_djangojob' not in tables:
                return
            from . import scheduler
            scheduler.start()
        except Exception:
            pass