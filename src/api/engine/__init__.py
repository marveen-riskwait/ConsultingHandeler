"""Compliance engines: audit, risk, rules and the event bus.

These modules implement the DATA -> EVENT -> RULE -> RISK -> WORKFLOW -> AUDIT
spine. Routes and Celery tasks call into here; they never contain the logic
themselves.
"""
