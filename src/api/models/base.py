"""Shared SQLAlchemy foundation for the domain models.

All model modules import `db` and `utcnow` from here. Cross-module relationships
use string class names (e.g. Mapped["User"]) which SQLAlchemy resolves through
the shared class registry — no cross-module imports needed.
"""
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow():
    # Naive UTC: the DateTime columns are timezone-naive, and drivers (notably
    # sqlite) drop tzinfo on round-trip. Keeping everything naive-UTC avoids
    # "can't compare offset-naive and offset-aware datetimes" at read time.
    return datetime.now(timezone.utc).replace(tzinfo=None)
