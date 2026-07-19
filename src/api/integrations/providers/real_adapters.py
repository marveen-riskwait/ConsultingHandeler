"""Adapters prepared for real vendors (Sumsub, Trulioo, ComplyAdvantage).

These are wired for the normalization + webhook pipeline but do NOT call the
vendors until credentials are configured — they never invent data. `health_check`
reports DEGRADED when the API key is missing; live calls raise a clear error so a
failed integration is never silently ignored. Webhook normalization maps each
vendor's payload shape into the internal NormalizedResult.
"""
from api.integrations.providers.base import (
    KYCProvider, AMLScreeningProvider, NormalizedResult,
)


class _CredentialedProvider:
    required_credential = "api_key"

    def _require_key(self):
        if not self.credentials.get(self.required_credential):
            raise RuntimeError(
                f"{self.adapter_key}: missing credential '{self.required_credential}'. "
                "Configure it in Administration → Integrations before use.")

    def health_check(self):
        if not self.credentials.get(self.required_credential):
            return ("DEGRADED", "API key not configured")
        return ("UP", "credentials present (live check not run)")


class SumsubKYCProvider(_CredentialedProvider, KYCProvider):
    adapter_key = "sumsub"

    def create_verification(self, subject):
        self._require_key()
        raise RuntimeError("Sumsub live verification not enabled in this build")

    def normalize_webhook(self, payload):
        answer = (payload.get("reviewResult", {}).get("reviewAnswer") or "").upper()
        status = {"GREEN": "PASSED", "RED": "FAILED"}.get(answer, "PENDING")
        return NormalizedResult(
            provider="sumsub",
            provider_reference=payload.get("applicantId"),
            result_type=payload.get("type", "IDENTITY"),
            status=status, data=payload, raw=payload)


class TruliooKYCProvider(_CredentialedProvider, KYCProvider):
    adapter_key = "trulioo"

    def create_verification(self, subject):
        self._require_key()
        raise RuntimeError("Trulioo live verification not enabled in this build")

    def normalize_webhook(self, payload):
        record = (payload.get("Record") or {})
        status = "PASSED" if record.get("RecordStatus") == "match" else "FAILED"
        return NormalizedResult(
            provider="trulioo",
            provider_reference=payload.get("TransactionID"),
            result_type="IDENTITY", status=status, data=payload, raw=payload)


class ComplyAdvantageAMLProvider(_CredentialedProvider, AMLScreeningProvider):
    adapter_key = "comply_advantage"

    def screen_subject(self, subject):
        self._require_key()
        raise RuntimeError("ComplyAdvantage live screening not enabled in this build")

    def normalize_webhook(self, payload):
        match = payload.get("match_status") or payload.get("status")
        status = "MATCH" if match in ("potential_match", "true_positive") else "PASSED"
        return NormalizedResult(
            provider="comply_advantage",
            provider_reference=str(payload.get("search_id") or payload.get("id") or ""),
            result_type="SCREENING", status=status, data=payload, raw=payload)
