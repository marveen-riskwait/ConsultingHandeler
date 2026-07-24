"""AbuseIPDB fraud-signal adapter.

An onboarding IP can carry fraud signals: abuse reports, Tor exit nodes,
hosting/VPN usage. AbuseIPDB's free tier answers a /check with an abuse
confidence score (0-100) — good enough to raise a first-pass signal.

Same shape as the Companies House adapter: the key comes from a stored
provider credential (`api_key`) or the ABUSEIPDB_API_KEY env var, and every
call reports a clear "no key" error until one is configured, so the provider
can ship enabled and dormant — ready to key.
"""
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://api.abuseipdb.com/api/v2/check"
_TIMEOUT = 15


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class AbuseIPDBProvider:
    adapter_key = "abuseipdb"

    def __init__(self, config=None, credentials=None):
        self.config = config or {}
        self.credentials = credentials or {}

    def _api_key(self):
        key = (self.credentials.get("api_key")
               or os.getenv("ABUSEIPDB_API_KEY") or "")
        return key.strip() or None

    def health_check(self):
        if not self._api_key():
            return ("DEGRADED", "API key not configured")
        return ("UP", "credentials present")

    def check(self, ip, max_age_days=90):
        """Return a normalised fraud signal for an IP, or raise RuntimeError
        with an actionable message when the key is missing / rejected."""
        key = self._api_key()
        if not key:
            raise RuntimeError(
                "abuseipdb: missing API key. Set it in Administration → "
                "Integrations (api_key) or the ABUSEIPDB_API_KEY env var.")
        url = API_URL + "?" + urllib.parse.urlencode(
            {"ipAddress": ip, "maxAgeInDays": max_age_days})
        req = urllib.request.Request(url, headers={
            "Key": key, "Accept": "application/json",
            "User-Agent": "ComplianceOS/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT,
                                        context=_ssl_context()) as r:
                payload = json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError("abuseipdb: API key rejected (401/403)")
            if exc.code == 422:
                raise RuntimeError("abuseipdb: invalid IP address")
            raise RuntimeError(f"abuseipdb: HTTP {exc.code}")
        data = payload.get("data") or {}
        score = int(data.get("abuseConfidenceScore") or 0)
        return {
            "ip": data.get("ipAddress") or ip,
            "abuse_score": score,
            "total_reports": int(data.get("totalReports") or 0),
            "country": data.get("countryCode"),
            "is_tor": bool(data.get("isTor")),
            "usage_type": data.get("usageType"),
            "isp": data.get("isp"),
            "domain": data.get("domain"),
        }
