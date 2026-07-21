"""French registry enrichment via recherche-entreprises.api.gouv.fr.

Fully free, no key: SIREN/SIRET, legal form, activity (NAF), registered
office, and the published company officers (dirigeants).
"""
import urllib.parse

from api.integrations.enrichment.base import EnrichmentSource, result, get_json

API = "https://recherche-entreprises.api.gouv.fr/search"


class FrenchRegistrySource(EnrichmentSource):
    name = "france_registry"

    def applies(self, customer):
        country = (customer.country or "").strip().lower()
        return (customer.customer_type == "COMPANY"
                and country in ("france", "fr", "french republic"))

    def run(self, customer, context=None):
        q = urllib.parse.quote(customer.name)
        data = get_json(f"{API}?q={q}&per_page=3")
        results = data.get("results") or []
        if not results:
            return result(self.name, ok=False,
                          detail="No match in the French registry.")

        best = results[0]
        siege = best.get("siege") or {}
        fields = {
            "legal_name": best.get("nom_complet") or best.get("nom_raison_sociale"),
            "registration_number": best.get("siren"),
            "legal_form": best.get("nature_juridique"),
            "date_of_incorporation": best.get("date_creation"),
            "country_of_incorporation": "France",
            "nace_code": best.get("activite_principale"),
            "registered_office": ", ".join(
                v for v in [siege.get("adresse"), siege.get("code_postal"),
                            siege.get("libelle_commune")] if v),
            "number_of_employees": best.get("tranche_effectif_salarie"),
        }
        fields = {k: {"value": str(v), "confidence": 0.95}
                  for k, v in fields.items() if v}

        parties = []
        for d in (best.get("dirigeants") or [])[:15]:
            name = (" ".join(x for x in [d.get("prenoms"), d.get("nom")] if x)
                    or d.get("denomination"))
            if not name:
                continue
            parties.append({
                "name": name,
                "kind": "ORGANIZATION" if d.get("denomination") else "PERSON",
                "relationship_type": "DIRECTOR", "percentage": 0.0,
                "nationality": d.get("nationalite"), "country": "France",
            })

        return result(self.name,
                      detail=f"SIREN {best.get('siren')} "
                             f"({best.get('etat_administratif', '?')})",
                      fields=fields, parties=parties)
