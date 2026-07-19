"""Celery instance bound to the Flask application context.

Run a worker (from the ``src`` directory) with:

    celery -A api.celery_app.celery worker --loglevel=info

and the periodic beat scheduler with:

    celery -A api.celery_app.celery beat --loglevel=info
"""
import os

from celery import Celery
from celery.schedules import crontab


def _broker_url():
    return (os.getenv("CELERY_BROKER_URL")
            or os.getenv("REDIS_URL")
            or "redis://localhost:6379/0")


def _backend_url():
    return (os.getenv("CELERY_RESULT_BACKEND")
            or os.getenv("REDIS_URL")
            or _broker_url())


celery = Celery(
    "compliance",
    broker=_broker_url(),
    backend=_backend_url(),
    include=["api.tasks"],
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        # Continuous monitoring sweeps.
        "daily-document-expiry-check": {
            "task": "api.tasks.check_document_expiry",
            "schedule": crontab(hour=6, minute=0),
        },
        "daily-review-deadline-check": {
            "task": "api.tasks.check_review_deadlines",
            "schedule": crontab(hour=6, minute=15),
        },
        "weekly-high-risk-rescreening": {
            "task": "api.tasks.rescreen_high_risk",
            "schedule": crontab(hour=3, minute=0, day_of_week=1),
        },
    },
)


def init_flask_context():
    """Wrap every task in a Flask app context so DB access works in the worker.

    Imported here (not at module top) to avoid an import cycle: app.py imports
    the API which lazily imports tasks/celery.
    """
    from app import app as flask_app

    class ContextTask(celery.Task):
        abstract = True

        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return flask_app


# Bind the app context when the worker imports this module. During the web
# import the Flask `app` object isn't defined yet (app.py imports the API before
# assigning `app`); that's expected and harmless — the worker re-imports cleanly.
try:
    init_flask_context()
except Exception:  # pragma: no cover - expected during web import
    pass
