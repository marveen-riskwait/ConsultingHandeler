"""Abstract LLM provider contract + shared HTTP plumbing for REST adapters."""
import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field

_TIMEOUT = 60


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def get_json(url, headers=None):
    """GET JSON with the same error shape as post_json."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT,
                                    context=_ssl_context()) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode()[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}")


def post_json(url, payload, headers=None):
    """POST JSON, return parsed JSON. Raises RuntimeError with the provider's
    error body on HTTP failure so the route can surface a readable message."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT,
                                    context=_ssl_context()) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode()[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}")


@dataclass
class LLMResult:
    """A single assistant completion + bookkeeping for the audit trail."""
    text: str
    model: str = ""
    usage: dict = field(default_factory=dict)


class LLMProvider:
    """Adapters implement `complete()`.

    `system` is the persona/guardrails string. `messages` is a list of
    {"role": "user"|"assistant", "content": str} in chronological order.
    """

    name = "base"
    available = False

    def complete(self, system, messages):  # pragma: no cover - interface
        raise NotImplementedError
