"""Enrichment engine — fills customer files from public sources automatically.

For each applicable source (registries, LEI, adverse media…):
- observations land in ProfileField with provenance (source="registry:<name>",
  auto-verified: a registry is a trusted, independent source);
- human-declared values are NEVER overwritten — a difference raises an
  ENRICHMENT_DISCREPANCY event instead (rule -> verification task);
- registry officers/PSC become Parties/Ownership edges through party_service,
  so UBO recomputation and change events fire exactly as for manual entry;
- adverse-media hits emit ADVERSE_MEDIA_DETECTED with the articles as payload
  (rule -> review task, alert), never a silent boolean.

Designed to run out-of-band (Celery) at scale; the manual endpoint runs it
inline so analysts get an immediate report.
"""
from api.models import db, ProfileField, Party, OwnershipRelationship
from api.engine import audit, kyc_service, party_service, requirement_engine
from api.engine.events import emit_event
from api.integrations.enrichment import sources_for

# Sources a human typed — enrichment must not clobber these.
_HUMAN_SOURCES = ("manual", "kyc_form")


def _existing_related_names(customer):
    """Names already attached to the customer's ownership graph (any role)."""
    root = customer.root_party_id
    if not root:
        return set()
    edges = OwnershipRelationship.query.filter_by(owned_party_id=root,
                                                  active=True).all()
    owners = Party.query.filter(
        Party.id.in_([e.owner_party_id for e in edges] or [0])).all()
    return {(p.name or "").strip().lower() for p in owners}


def _apply_fields(customer, source_name, fields, actor, report):
    current = {f.field_key: f for f in
               ProfileField.query.filter_by(customer_id=customer.id).all()}
    for key, obs in fields.items():
        value = (str(obs.get("value")) if obs.get("value") is not None else "").strip()
        if not value:
            continue
        existing = current.get(key)
        if existing and (existing.value or "").strip():
            if (existing.value or "").strip().lower() == value.lower():
                continue  # same information — nothing to do
            if existing.source in _HUMAN_SOURCES or existing.verified:
                # Declared/verified data differs from the registry: flag it.
                report["discrepancies"].append(
                    {"field": key, "declared": existing.value, "found": value,
                     "source": source_name})
                continue
        kyc_service.set_field(customer, key, value,
                              source=f"registry:{source_name}",
                              confidence=obs.get("confidence", 0.9),
                              actor=actor)
        report["fields_filled"] += 1


def _apply_parties(customer, source_name, parties, actor, report):
    known = _existing_related_names(customer)
    for p in parties:
        name = (p.get("name") or "").strip()
        if not name or name.lower() in known:
            continue
        party_service.add_related_party(
            customer,
            owner_name=name,
            owner_kind=p.get("kind", "PERSON"),
            relationship_type=p.get("relationship_type", "SHAREHOLDER"),
            percentage=float(p.get("percentage") or 0.0),
            country=p.get("country"),
            nationality=p.get("nationality"),
            actor=actor,
        )
        known.add(name.lower())
        report["parties_added"] += 1


def enrich(customer, actor=None):
    """Run every applicable source; returns a human-readable report dict."""
    report = {"sources": [], "fields_filled": 0, "parties_added": 0,
              "media_hits": 0, "discrepancies": []}

    for source in sources_for(customer):
        try:
            out = source.run(customer)
        except Exception as exc:
            out = {"source": source.name, "ok": False,
                   "detail": f"error: {exc}", "fields": {}, "parties": [],
                   "media": []}
        report["sources"].append({"source": out["source"], "ok": out["ok"],
                                  "detail": out.get("detail")})
        _apply_fields(customer, out["source"], out.get("fields") or {},
                      actor, report)
        _apply_parties(customer, out["source"], out.get("parties") or [],
                       actor, report)

        media = out.get("media") or []
        if media:
            report["media_hits"] += len(media)
            emit_event("ADVERSE_MEDIA_DETECTED", customer_id=customer.id,
                       severity="HIGH" if any(m.get("severity") == "HIGH"
                                              for m in media) else "MEDIUM",
                       source=f"enrichment:{out['source']}", actor=actor,
                       payload={"articles": media,
                                "article": media[0].get("title"),
                                "category": "adverse media (enrichment)"})

    for d in report["discrepancies"]:
        emit_event("ENRICHMENT_DISCREPANCY", customer_id=customer.id,
                   severity="MEDIUM", source=f"enrichment:{d['source']}",
                   actor=actor, payload=d)

    # Requirements recompute so the completeness bar reflects the new data.
    summary = requirement_engine.summary(customer)

    parts = [f"{report['fields_filled']} field(s) filled",
             f"{report['parties_added']} related part(y/ies) added"]
    if report["media_hits"]:
        parts.append(f"{report['media_hits']} adverse-media hit(s)")
    if report["discrepancies"]:
        parts.append(f"{len(report['discrepancies'])} discrepanc(y/ies) flagged")
    ok_sources = [s["source"] for s in report["sources"] if s["ok"]]
    parts.append(f"sources: {', '.join(ok_sources) or 'none matched'}")
    report["summary"] = " · ".join(parts)
    report["completeness"] = summary

    audit.record("CUSTOMER_ENRICHED", "customer", customer.id, actor=actor,
                 new_value=report["summary"])
    emit_event("ENRICHMENT_COMPLETED", customer_id=customer.id,
               severity="INFO", source="enrichment", actor=actor,
               payload={"summary": report["summary"],
                        "sources": report["sources"]})
    return report
