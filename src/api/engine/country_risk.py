"""Geography scoring driven by the official lists instead of a guess.

The risk methodology used to carry one GEOGRAPHY factor over a hardcoded set of
six countries. That is indefensible in a review: an examiner asks where the
list comes from, and "somebody typed it" is not an answer.

This replaces it with three factors mapped to the actual regimes, each scored
according to what it legally means:

  FATF Call for Action    +35  countermeasures apply; the strongest signal
  EU high-risk third      +25  EDD is mandatory under the AML directives
  FATF Increased Monitor. +15  strategic deficiencies, enhanced vigilance
  Institution-defined     +20  the firm's own risk appetite, empty by default

The fourth one matters. Official lists are narrower than most firms' actual
exposure — Russia, for instance, is heavily sanctioned but sits on neither the
FATF nor the EU list. Rather than quietly padding a regulator's list with our
own opinions, the firm's additions live in their own factor: an examiner can
see exactly which countries came from FATF and which came from the institution.

They stack on purpose — a country on both the FATF black list and the EU list
is riskier than one on either alone, and the score shows why, factor by factor,
because the assessment records each contribution separately.

Sync is idempotent and only touches the geography factors, so an officer's
manual edits to other factors (or to these impacts) are never overwritten:
country membership is refreshed, the weights stay as configured.
"""
from api.models import db, RiskMethodology, RiskFactor
from api.integrations import countryrisk

# code -> (dataset, label, default impact)
GEOGRAPHY_FACTORS = {
    "GEO_FATF_ACTION": ("FATF_CALL_FOR_ACTION",
                        "FATF Call for Action jurisdiction", 35),
    "GEO_EU_HIGH_RISK": ("EU_HIGH_RISK",
                         "EU high-risk third country", 25),
    "GEO_FATF_MONITORING": ("FATF_INCREASED_MONITORING",
                            "FATF Increased Monitoring jurisdiction", 15),
}
# Owned by the compliance team, never overwritten by a sync.
INSTITUTION_CODE = "GEO_INSTITUTION"
INSTITUTION_LABEL = "Institution-defined high-risk jurisdiction"
INSTITUTION_IMPACT = 20
# The single hardcoded factor these replace.
LEGACY_CODE = "GEOGRAPHY"


def active_methodology(organization_id=None):
    q = RiskMethodology.query.filter_by(active=True)
    own = q.filter_by(organization_id=organization_id).first() if organization_id else None
    return own or q.filter_by(organization_id=None).first()


def sync(organization_id=None, prefer_live=True, retire_legacy=True,
         institution_countries=None):
    """Refresh the geography factors from the official lists. Returns a report."""
    methodology = active_methodology(organization_id)
    if methodology is None:
        return {"synced": [], "reason": "no active methodology"}

    lists = countryrisk.all_lists(prefer_live=prefer_live)
    report = []
    for code, (dataset, label, default_impact) in GEOGRAPHY_FACTORS.items():
        data = lists[dataset]
        factor = RiskFactor.query.filter_by(methodology_id=methodology.id,
                                            code=code).first()
        if factor is None:
            factor = RiskFactor(methodology_id=methodology.id, code=code,
                                label=label, impact=default_impact,
                                condition_type="COUNTRY_IN")
            db.session.add(factor)
        # Membership is refreshed; the impact stays whatever it was configured to.
        factor.condition_value = {"values": sorted(data["countries"]),
                                  "as_of": data["as_of"],
                                  "source_url": data["source_url"]}
        report.append({"code": code, "label": label,
                       "countries": len(data["countries"]),
                       "as_of": data["as_of"], "stale": data["stale"],
                       "live": data.get("live", False)})

    # The firm's own list is created once, then left alone: sync refreshes the
    # official lists, it does not have opinions about the firm's risk appetite.
    own = RiskFactor.query.filter_by(methodology_id=methodology.id,
                                     code=INSTITUTION_CODE).first()
    if own is None:
        own = RiskFactor(methodology_id=methodology.id, code=INSTITUTION_CODE,
                         label=INSTITUTION_LABEL, impact=INSTITUTION_IMPACT,
                         condition_type="COUNTRY_IN",
                         condition_value={"values": list(institution_countries or [])})
        db.session.add(own)
    report.append({"code": INSTITUTION_CODE, "label": INSTITUTION_LABEL,
                   "countries": len(own.condition_value.get("values", [])),
                   "as_of": None, "stale": False, "live": False})

    if retire_legacy:
        legacy = RiskFactor.query.filter_by(methodology_id=methodology.id,
                                            code=LEGACY_CODE).first()
        if legacy is not None:
            db.session.delete(legacy)

    db.session.commit()
    return {"methodology": methodology.version, "synced": report}


def status(prefer_live=False, organization_id=None):
    """What the geography lists are and how fresh — for the admin UI."""
    lists = countryrisk.all_lists(prefer_live=prefer_live)
    rows = [{"code": code, "dataset": dataset, "label": lists[dataset]["label"],
             "impact": default_impact,
             "countries": sorted(lists[dataset]["countries"]),
             "as_of": lists[dataset]["as_of"],
             "stale": lists[dataset]["stale"],
             "source_url": lists[dataset]["source_url"]}
            for code, (dataset, _l, default_impact) in GEOGRAPHY_FACTORS.items()]

    methodology = active_methodology(organization_id)
    own = (RiskFactor.query.filter_by(methodology_id=methodology.id,
                                      code=INSTITUTION_CODE).first()
           if methodology else None)
    rows.append({
        "code": INSTITUTION_CODE, "dataset": None, "label": INSTITUTION_LABEL,
        "impact": own.impact if own else INSTITUTION_IMPACT,
        "countries": sorted((own.condition_value or {}).get("values", [])) if own else [],
        "as_of": None, "stale": False, "source_url": None,
        "editable": True,
    })
    return rows
