"""Watchlist ingestion + local screening against public sanctions lists.

`ingest()` pulls one public source (OFAC / UN / EU) — live when the network
allows, bundled sample otherwise — and upserts it into SanctionedEntity, with a
WatchlistImport row for provenance. `search()` is the matching primitive used
both by the local screening provider and the admin search UI.
"""
import os
import re
import unicodedata
from difflib import SequenceMatcher

from sqlalchemy import or_, cast, String

from api.models import (db, SanctionedEntity, WatchlistImport,
                        SanctionedWallet, utcnow)
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


# How close a misspelling must be before it counts. 0.84 keeps "Sberbanc of
# Russia" and drops unrelated names; lower it to catch more (and review more
# false positives), raise it to be stricter. Sanctions evasion is spelled
# deliberately wrong, so some tolerance is not optional.
FUZZY_THRESHOLD = float(os.getenv("SCREENING_FUZZY_THRESHOLD", "0.84"))
# Fuzzy hits stay below the exact tiers (95 name / 90 alias / 82 token subset),
# so a reviewer can always tell "same name" from "looks like".
_FUZZY_CEILING = 88
# Candidate pull uses the first characters of each token, so a typo at the end
# of a word still retrieves the record ("sberbanc" -> "sber" -> "sberbank").
_STEM = 4


def _ratio(a, b):
    return SequenceMatcher(None, a, b).ratio()


def _fuzzy_score(q, q_tokens, names):
    """Best similarity between the query and any spelling of the entity.

    Two views, because names go wrong in two ways: the whole string (typos,
    transliteration) and word by word (a middle name dropped, words reordered).
    """
    best = 0.0
    for name in names:
        if not name:
            continue
        best = max(best, _ratio(q, name))
        # Word-by-word only with two or more significant tokens: with a single
        # one this degenerates into "any entity containing that word", which is
        # fine for a search box and far too noisy for automatic screening.
        if len(q_tokens) >= 2:
            n_tokens = _tokens(name)
            if n_tokens:
                pairs = [max(_ratio(t, n) for n in n_tokens) for t in q_tokens]
                best = max(best, sum(pairs) / len(pairs))
    return best


def _upsert_wallets(row, rec):
    """Persist the sanctioned wallets a record carries (OFAC publishes them)."""
    for w in (rec.wallets or []):
        address = (w.get("address") or "").strip()
        asset = (w.get("asset") or "").strip().upper()
        if not address or not asset:
            continue
        normalized = address.lower()
        wallet = (SanctionedWallet.query
                  .filter_by(source=rec.source, asset=asset,
                             address_normalized=normalized).first())
        if wallet is None:
            wallet = SanctionedWallet(source=rec.source, asset=asset,
                                      address_normalized=normalized)
            db.session.add(wallet)
        wallet.address = address[:160]
        wallet.entity_id = row.id
        wallet.entity_name = row.name
        wallet.programs = row.programs or []
        wallet.imported_at = utcnow()


def screen_wallet(address):
    """Exact lookup of a blockchain address against the sanctioned wallets.

    Exact on purpose: a wallet address is a checksum, not a name. "Close to" a
    sanctioned address means a different wallet, so fuzziness here would only
    manufacture false positives.
    """
    normalized = (address or "").strip().lower()
    if len(normalized) < 20:
        return []
    return (SanctionedWallet.query
            .filter_by(address_normalized=normalized)
            .order_by(SanctionedWallet.source).all())


def wallet_stats():
    rows = SanctionedWallet.query.all()
    by_asset = {}
    for w in rows:
        by_asset[w.asset] = by_asset.get(w.asset, 0) + 1
    return {"total": len(rows),
            "by_asset": dict(sorted(by_asset.items(), key=lambda kv: -kv[1]))}


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
            db.session.flush()
            _upsert_wallets(row, rec)
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
        stem = t[:_STEM]
        conditions.append(SanctionedEntity.name_normalized.like(f"%{stem}%"))
        conditions.append(aliases_text.like(f"%{stem}%"))
    candidates = (SanctionedEntity.query
                  .filter(or_(*conditions))
                  .limit(2000).all())

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
            else:
                names = [e.name_normalized] + list(e.aliases_normalized or [])
                similarity = _fuzzy_score(q, q_tokens, names)
                if similarity >= FUZZY_THRESHOLD:
                    score = min(int(round(similarity * _FUZZY_CEILING)),
                                _FUZZY_CEILING)
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


def suggest(query, limit=25):
    """Type-ahead over the watchlist: substring, not similarity.

    Deliberately a different primitive from `search()`. While typing a name you
    want "everything containing these letters, narrowing as I type" — cheap,
    predictable, ordered by where the fragment appears. Fuzzy scoring belongs to
    screening, where a deliberate misspelling must still be caught.
    """
    q = normalize_name(query)
    if len(q) < 3:
        return []
    aliases_text = cast(SanctionedEntity.aliases_normalized, String)
    rows = (SanctionedEntity.query
            .filter(or_(SanctionedEntity.name_normalized.like(f"%{q}%"),
                        aliases_text.like(f"%{q}%")))
            .limit(limit * 8).all())

    def rank(e):
        pos = e.name_normalized.find(q)
        if pos == 0:                       # starts with what you typed
            return (0, len(e.name_normalized))
        if pos > 0:                        # contains it
            return (1, pos)
        return (2, len(e.name_normalized))  # only an alias matched

    rows.sort(key=rank)
    return rows[:limit]
