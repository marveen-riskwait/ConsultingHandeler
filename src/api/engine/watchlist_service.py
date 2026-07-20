"""Watchlist ingestion + local screening against public sanctions lists.

`ingest()` pulls one public source (OFAC / UN / EU) — live when the network
allows, bundled sample otherwise — and upserts it into SanctionedEntity, with a
WatchlistImport row for provenance. `search()` is the matching primitive used
both by the local screening provider and the admin search UI.
"""
import re
import unicodedata

from sqlalchemy import or_, cast, String

from api.models import db, SanctionedEntity, WatchlistImport, utcnow
from api.engine import audit
from api.integrations.sanctions import get_source, all_sources

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")

# Very common corporate/name tokens that shouldn't drive a match on their own.
_STOPWORDS = {"the", "of", "and", "for", "ltd", "llc", "inc", "co", "company",
              "corp", "corporation", "sa", "ag", "gmbh", "plc", "group", "bank"}


def normalize_name(name):
    """Lowercase, strip accents/punctuation, collapse whitespace."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = _PUNCT.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


def _tokens(normalized):
    return {t for t in normalized.split(" ") if len(t) > 2 and t not in _STOPWORDS}


# ---------------------------------------------------------------------------- ingest
def ingest(source_code, actor=None, prefer_live=True, limit=None):
    """Ingest one source. Returns the WatchlistImport row."""
    imp = WatchlistImport(source=source_code.upper(),
                          actor_id=actor.id if actor else None)
    db.session.add(imp)
    db.session.flush()

    try:
        source = get_source(source_code)
        records, is_live = source.records(prefer_live=prefer_live, limit=limit)
        count = 0
        for rec in records:
            row = (SanctionedEntity.query
                   .filter_by(source=rec.source, external_id=str(rec.external_id))
                   .first())
            if row is None:
                row = SanctionedEntity(source=rec.source,
                                       external_id=str(rec.external_id))
                db.session.add(row)
            row.name = rec.name[:400]
            row.name_normalized = normalize_name(rec.name)[:400]
            row.entity_type = rec.entity_type
            row.aliases = rec.aliases or []
            row.aliases_normalized = [normalize_name(a) for a in (rec.aliases or [])]
            row.programs = rec.programs or []
            row.country = rec.country
            row.remarks = (rec.remarks or None)
            row.imported_at = utcnow()
            count += 1

        imp.status = "OK"
        imp.live = is_live
        imp.record_count = count
        imp.detail = f"{source.label}: {count} records ({'live' if is_live else 'bundled sample'})"
    except Exception as exc:
        imp.status = "FAILED"
        imp.detail = str(exc)[:300]

    imp.finished_at = utcnow()
    audit.record("WATCHLIST_IMPORT", "watchlist", imp.id, actor=actor,
                 new_value=f"{imp.source}:{imp.status}:{imp.record_count}")
    db.session.commit()
    return imp


def ingest_all(actor=None, prefer_live=True, limit=None):
    return [ingest(s.code, actor=actor, prefer_live=prefer_live, limit=limit)
            for s in all_sources()]


# ---------------------------------------------------------------------------- search
def search(name, limit=25):
    """Match a name against the local watchlist.

    Returns [(entity, score)] sorted by score desc. Scoring is deliberately
    simple and explainable: exact normalized name (95), exact alias (90),
    full token containment either way (82).
    """
    q = normalize_name(name)
    if not q:
        return []
    q_tokens = _tokens(q)

    # Candidate pull: exact name/alias, or any significant token appearing in
    # the name or the aliases. The JSON alias column is scanned as text — crude
    # but portable (SQLite + PostgreSQL); precision comes from Python scoring.
    aliases_text = cast(SanctionedEntity.aliases_normalized, String)
    conditions = [SanctionedEntity.name_normalized == q,
                  aliases_text.like(f'%{q}%')]
    for t in list(q_tokens)[:5]:
        conditions.append(SanctionedEntity.name_normalized.like(f"%{t}%"))
        conditions.append(aliases_text.like(f"%{t}%"))
    candidates = (SanctionedEntity.query
                  .filter(or_(*conditions))
                  .limit(500).all())

    scored = []
    for e in candidates:
        score = 0
        if e.name_normalized == q:
            score = 95
        elif q in (e.aliases_normalized or []):
            score = 90
        else:
            e_tokens = _tokens(e.name_normalized)
            for alias in (e.aliases_normalized or []):
                e_tokens |= _tokens(alias)
            if q_tokens and e_tokens and len(q_tokens) >= 2 and \
                    (q_tokens <= e_tokens or e_tokens <= q_tokens):
                score = 82
        if score:
            scored.append((e, score))

    scored.sort(key=lambda pair: -pair[1])
    return scored[:limit]


def stats():
    """Per-source record counts + the latest import for each."""
    out = []
    for src in all_sources():
        count = SanctionedEntity.query.filter_by(source=src.code).count()
        last = (WatchlistImport.query.filter_by(source=src.code)
                .order_by(WatchlistImport.id.desc()).first())
        out.append({
            "source": src.code,
            "label": src.label,
            "record_count": count,
            "last_import": last.serialize() if last else None,
        })
    return out
