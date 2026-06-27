import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bsa_project.settings")

app = Celery("bsa_project")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "cleanup-stuck-extractions": {
        "task": "statements.tasks.cleanup_stuck_extractions",
        "schedule": crontab(minute="*/5"),  # every 5 minutes
    },
}