"""Transaction monitoring — detectors over booked activity.

Each ingested transaction is run through a set of typology detectors. When one
fires, the transaction is flagged and a TRANSACTION_ALERT event is emitted onto
the compliance spine — the same rules engine + alert_service that already do
case creation, assignment routing, dedup and close-out. Nothing about routing
is reinvented here; this module only decides *whether a movement is unusual*.

Thresholds are read from the environment with sensible AML defaults so a
deployment can tighten them without a code change. High-risk counterparty
countries are read from the ACTIVE risk methodology's COUNTRY_IN factors — the
same data-driven list the risk engine uses, never a second hard-coded copy.
"""
import os
from datetime import timedelta

from api.models import db, Transaction, RiskFactor, utcnow
from api.engine import audit
from api.engine.events import emit_event
from api.engine.risk_engine import active_methodology


def _num(env, default):
    try:
        return float(os.getenv(env, default))
    except (TypeError, ValueError):
        return float(default)


# Reporting-currency thresholds (amount_base is in EUR — see the model note on
# the FX simplification).
LARGE_AMOUNT = _num("TM_LARGE_AMOUNT", 10000)          # single large movement
STRUCT_FLOOR = _num("TM_STRUCTURING_FLOOR", 8000)      # "just under" band start
STRUCT_MIN_COUNT = int(_num("TM_STRUCTURING_COUNT", 3))
STRUCT_WINDOW_DAYS = int(_num("TM_STRUCTURING_WINDOW_DAYS", 7))
VELOCITY_WINDOW_HOURS = int(_num("TM_VELOCITY_WINDOW_HOURS", 24))
VELOCITY_COUNT = int(_num("TM_VELOCITY_COUNT", 10))
VELOCITY_VOLUME = _num("TM_VELOCITY_VOLUME", 50000)
PASSTHROUGH_WINDOW_HOURS = int(_num("TM_PASSTHROUGH_WINDOW_HOURS", 48))
PASSTHROUGH_MIN = _num("TM_PASSTHROUGH_MIN", 10000)
PASSTHROUGH_RATIO = _num("TM_PASSTHROUGH_RATIO", 0.9)
CASH_THRESHOLD = _num("TM_CASH_THRESHOLD", 3000)


def _high_risk_countries(organization_id):
    """The set of high-risk countries from the active risk methodology's
    COUNTRY_IN factors — one source of truth shared with the risk engine."""
    # Reuse the risk engine's selector — it correctly falls back to the
    # system methodology (org query then IS NULL), which an IN (org, NULL)
    # filter would silently miss (SQL never matches NULL with IN).
    methodology = active_methodology(organization_id)
    if methodology is None:
        return set()
    # Query the factors directly rather than through methodology.factors: the
    # relationship collection can be a stale cache within a session that just
    # added a factor, whereas a fresh query always reflects committed rows.
    factors = (RiskFactor.query
               .filter(RiskFactor.methodology_id == methodology.id,
                       RiskFactor.active.is_(True),
                       RiskFactor.condition_type == "COUNTRY_IN")
               .all())
    countries = set()
    for f in factors:
        countries.update((f.condition_value or {}).get("values", []))
    return {c.strip().lower() for c in countries}


def _window(customer_id, since, direction=None):
    q = (Transaction.query
         .filter(Transaction.customer_id == customer_id,
                 Transaction.booked_at >= since))
    if direction:
        q = q.filter(Transaction.direction == direction)
    return q.all()


def _detect(tx, customer):
    """Return a list of {code, severity, detail} for the detectors that fire on
    this (already persisted) transaction."""
    fired = []
    amt = tx.amount_base or 0.0

    # 1. Large single movement.
    if amt >= LARGE_AMOUNT:
        fired.append({"code": "LARGE_AMOUNT", "severity": "HIGH",
                      "detail": f"{amt:,.0f} {tx.currency} in a single {tx.direction.lower()} "
                                f"movement (threshold {LARGE_AMOUNT:,.0f})"})

    # 2. High-risk counterparty country.
    hrc = _high_risk_countries(customer.organization_id)
    if tx.counterparty_country and tx.counterparty_country.strip().lower() in hrc:
        fired.append({"code": "HIGH_RISK_COUNTRY", "severity": "HIGH",
                      "detail": f"Counterparty in high-risk jurisdiction: "
                                f"{tx.counterparty_country}"})

    # 3. Structuring / smurfing: several same-direction movements sitting just
    #    below the reporting threshold within the window.
    if STRUCT_FLOOR <= amt < LARGE_AMOUNT:
        since = utcnow() - timedelta(days=STRUCT_WINDOW_DAYS)
        band = [t for t in _window(customer.id, since, tx.direction)
                if STRUCT_FLOOR <= (t.amount_base or 0) < LARGE_AMOUNT]
        if len(band) >= STRUCT_MIN_COUNT:
            fired.append({"code": "STRUCTURING", "severity": "HIGH",
                          "detail": f"{len(band)} {tx.direction.lower()} movements in "
                                    f"[{STRUCT_FLOOR:,.0f}–{LARGE_AMOUNT:,.0f}) within "
                                    f"{STRUCT_WINDOW_DAYS} days — possible structuring"})

    # 4. Velocity: unusual count or volume in a short window.
    since = utcnow() - timedelta(hours=VELOCITY_WINDOW_HOURS)
    recent = _window(customer.id, since)
    total = sum(t.amount_base or 0 for t in recent)
    if len(recent) >= VELOCITY_COUNT or total >= VELOCITY_VOLUME:
        fired.append({"code": "VELOCITY", "severity": "MEDIUM",
                      "detail": f"{len(recent)} movements totalling {total:,.0f} in "
                                f"{VELOCITY_WINDOW_HOURS}h"})

    # 5. Rapid pass-through: a large inbound quickly followed by outbound(s)
    #    returning most of it (funnel / layering).
    if tx.direction == "OUTBOUND" and amt > 0:
        since = utcnow() - timedelta(hours=PASSTHROUGH_WINDOW_HOURS)
        inbound = sum(t.amount_base or 0 for t in _window(customer.id, since, "INBOUND"))
        outbound = sum(t.amount_base or 0 for t in _window(customer.id, since, "OUTBOUND"))
        if inbound >= PASSTHROUGH_MIN and outbound >= inbound * PASSTHROUGH_RATIO:
            fired.append({"code": "RAPID_PASSTHROUGH", "severity": "HIGH",
                          "detail": f"{outbound:,.0f} out against {inbound:,.0f} in within "
                                    f"{PASSTHROUGH_WINDOW_HOURS}h — pass-through pattern"})

    # 6. Cash intensity.
    if (tx.method or "").upper() == "CASH" and amt >= CASH_THRESHOLD:
        fired.append({"code": "CASH_INTENSIVE", "severity": "MEDIUM",
                      "detail": f"Cash movement of {amt:,.0f} {tx.currency} "
                                f"(threshold {CASH_THRESHOLD:,.0f})"})
    return fired


_SEV_RANK = {"MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def ingest(customer, data, actor=None):
    """Persist one transaction and monitor it. Idempotent on external_id.

    Returns (transaction, fired_detectors). When any detector fires, a
    TRANSACTION_ALERT event is emitted (one per flagged transaction); the
    alert_service then dedups to a single open alert per customer, so a burst
    of unusual movements is one thing to look at, not fifty."""
    ext = (data.get("external_id") or "").strip() or None
    if ext:
        existing = (Transaction.query
                    .filter_by(organization_id=customer.organization_id,
                               external_id=ext).first())
        if existing is not None:
            return existing, []      # already ingested — never double-count

    amount = float(data.get("amount") or 0)
    currency = (data.get("currency") or "EUR").upper()[:3]
    # Honest FX simplification: base == amount unless a rate is supplied.
    rate = float(data.get("fx_rate") or (1.0 if currency == "EUR" else 1.0))
    booked = data.get("booked_at")
    from datetime import datetime
    if isinstance(booked, str):
        try:
            booked = datetime.fromisoformat(booked.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            booked = utcnow()
    tx = Transaction(
        organization_id=customer.organization_id,
        customer_id=customer.id,
        external_id=ext,
        direction=(data.get("direction") or "INBOUND").upper(),
        amount=amount,
        currency=currency,
        amount_base=round(amount * rate, 2),
        method=(data.get("method") or None),
        counterparty_name=(data.get("counterparty_name") or None),
        counterparty_country=(data.get("counterparty_country") or None),
        reference=(data.get("reference") or None),
        booked_at=booked or utcnow(),
    )
    db.session.add(tx)
    db.session.flush()

    fired = _detect(tx, customer)
    tx.flags = [f["code"] for f in fired]
    tx.flagged = bool(fired)
    audit.record("TRANSACTION_INGESTED", "customer", customer.id, actor=actor,
                 new_value=f"{tx.direction} {amount:,.0f} {currency}"
                           + (f" · flags: {', '.join(tx.flags)}" if fired else ""))
    db.session.commit()

    if fired:
        severity = max((f["severity"] for f in fired),
                       key=lambda s: _SEV_RANK.get(s, 0))
        emit_event("TRANSACTION_ALERT", customer_id=customer.id,
                   severity=severity, source="transaction_monitoring", actor=actor,
                   payload={"transaction_id": tx.id,
                            "direction": tx.direction,
                            "amount": amount, "currency": currency,
                            "amount_base": tx.amount_base,
                            "counterparty_name": tx.counterparty_name,
                            "counterparty_country": tx.counterparty_country,
                            "method": tx.method,
                            "detectors": fired})
    return tx, fired
