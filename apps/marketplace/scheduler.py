# apps/marketplace/scheduler.py

from apscheduler.schedulers.background import BackgroundScheduler
from django_apscheduler.jobstores import DjangoJobStore
from django_apscheduler.models import DjangoJobExecution
import logging

logger = logging.getLogger(__name__)


def process_recurring_orders_job():
    from apps.marketplace.views import process_recurring_orders
    logger.info("Running recurring orders job...")
    process_recurring_orders()
    logger.info("Recurring orders job complete.")


def start():
    scheduler = BackgroundScheduler()
    scheduler.add_jobstore(DjangoJobStore(), "default")

    scheduler.add_job(
        process_recurring_orders_job,
        trigger="cron",
        hour=6,
        minute=0,
        id="process_recurring_orders",
        max_instances=1,
        replace_existing=True,
    )

    logger.info("Starting scheduler...")
    scheduler.start()