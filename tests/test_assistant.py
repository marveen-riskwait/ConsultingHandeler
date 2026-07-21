"""Compliance Copilot: conversation lifecycle, replies, context, isolation.

Runs against the deterministic MockProvider (no ANTHROPIC_API_KEY), so no
network is needed.
"""
import pytest

from conftest import auth


def _cid(client, token, name):
    r = client.get("/api/customers", headers=auth(token))
    return next(c["id"] for c in r.get_json() if c["name"] == name)


def test_provider_resolution_from_env(monkeypatch):
    """AI_PROVIDER wins; otherwise whichever key is present; else mock."""
    from api.integrations import ai
    try:
        for var in ("AI_PROVIDER", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                    "OPENAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)

        ai.reset_llm()
        assert ai.get_llm().name == "mock"

        monkeypatch.setenv("GEMINI_API_KEY", "dummy")
        ai.reset_llm()
        assert ai.get_llm().name == "gemini"

        monkeypatch.setenv("OPENAI_API_KEY", "dummy")  # gemini still wins
        ai.reset_llm()
        assert ai.get_llm().name == "gemini"

        monkeypatch.setenv("AI_PROVIDER", "openai")    # explicit choice wins
        ai.reset_llm()
        assert ai.get_llm().name == "openai"

        monkeypatch.setenv("AI_PROVIDER", "mock")
        ai.reset_llm()
        assert ai.get_llm().name == "mock"

        # A provider that can't initialize (SDK missing, bad env) must fall
        # back instead of 500ing the Copilot (the Codespace anthropic bug).
        class Broken:
            def __init__(self):
                raise ModuleNotFoundError("No module named 'anthropic'")
        monkeypatch.setattr(ai, "ClaudeProvider", Broken)
        monkeypatch.setenv("AI_PROVIDER", "claude")
        ai.reset_llm()
        assert ai.get_llm().name == "mock"     # explicit-but-broken -> mock
        monkeypatch.delenv("AI_PROVIDER")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")  # auto: skip broken…
        ai.reset_llm()
        assert ai.get_llm().name == "gemini"   # …and use the next available
    finally:
        ai.reset_llm()  # leave the cached provider clean for other tests


def test_gemini_auto_discovers_and_falls_back(monkeypatch):
    """Gemini adapter: lists the key's models, walks candidates on 404/429,
    caches the first one that answers (Google model-churn resilience)."""
    from api.integrations.ai import gemini as gm

    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    monkeypatch.setattr(gm, "get_json", lambda url, headers=None: {
        "models": [
            {"name": "models/gemini-2.5-flash",
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.0-flash",
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-embedding-001",
             "supportedGenerationMethods": ["embedContent"]},
        ]})

    calls = []
    def fake_post(url, payload, headers=None):
        model = url.split("/")[-1].split(":")[0]
        calls.append(model)
        if model == "gemini-2.5-flash":
            raise RuntimeError('HTTP 429: {"error": {"code": 429}}')
        return {"candidates": [{"content": {"parts": [{"text": "ok!"}]}}],
                "usageMetadata": {"promptTokenCount": 5,
                                  "candidatesTokenCount": 2}}
    monkeypatch.setattr(gm, "post_json", fake_post)

    p = gm.GeminiProvider()
    result = p.complete("sys", [{"role": "user", "content": "hi"}])
    assert result.text == "ok!" and result.model == "gemini-2.0-flash"
    # 2.5 got the 429, 2.0 answered; the embedding model was never considered.
    assert calls == ["gemini-2.5-flash", "gemini-2.0-flash"]

    # Second call goes straight to the cached working model.
    p.complete("sys", [{"role": "user", "content": "again"}])
    assert calls[-1] == "gemini-2.0-flash" and len(calls) == 3


def test_gemini_auth_errors_are_actionable_and_not_retried(monkeypatch):
    """401/403 apply to every model, so the adapter must stop and explain
    rather than walking candidates (the user's real Codespace failure)."""
    from api.integrations.ai import gemini as gm

    monkeypatch.setenv("GEMINI_API_KEY", '  "AIzaPADDED"  ')  # quotes + spaces
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    p = gm.GeminiProvider()
    assert p.api_key == "AIzaPADDED", "env padding must be stripped"

    monkeypatch.setattr(gm, "get_json", lambda url, headers=None: {
        "models": [{"name": "models/gemini-2.5-flash",
                    "supportedGenerationMethods": ["generateContent"]}]})
    calls = []

    def unauthorised(url, payload, headers=None):
        calls.append(url)
        raise RuntimeError('HTTP 401: {"error": {"status": "UNAUTHENTICATED"}}')
    monkeypatch.setattr(gm, "post_json", unauthorised)

    with pytest.raises(RuntimeError) as excinfo:
        p.complete("sys", [{"role": "user", "content": "hi"}])
    message = str(excinfo.value)
    assert "rejected the API key" in message
    assert "aistudio.google.com" in message
    assert len(calls) == 1, "an auth failure must not be retried on other models"


def test_assistant_check_endpoint(client, tokens):
    """The Copilot can self-report its provider status (mock in tests)."""
    r = client.post("/api/assistant/check",
                    headers=auth(tokens["analyst@test.io"]))
    assert r.status_code == 200
    body = r.get_json()
    assert body["provider"] == "mock" and body["ok"] is True
    assert "Demo mode" in body["detail"]


def test_meta_reports_mock_provider_and_prompts(client, tokens):
    t = tokens["analyst@test.io"]
    r = client.get("/api/assistant/meta", headers=auth(t))
    assert r.status_code == 200
    body = r.get_json()
    assert body["provider"] == "mock"  # no ANTHROPIC_API_KEY in tests
    assert len(body["suggested_prompts"]) >= 1


def test_conversation_create_send_and_persist(client, tokens):
    t = tokens["analyst@test.io"]
    conv = client.post("/api/assistant/conversations", headers=auth(t),
                       json={}).get_json()
    assert conv["id"] and conv["messages"] == []

    r = client.post(f"/api/assistant/conversations/{conv['id']}/messages",
                    headers=auth(t), json={"content": "Draft a SAR narrative"})
    assert r.status_code == 201
    reply = r.get_json()["reply"]
    assert reply["role"] == "assistant" and reply["content"]
    # The reply must reflect the user's message — proves the just-added turn was
    # actually sent to the model (regression: empty-history bug).
    assert "SAR" in reply["content"]

    # Reloading returns both turns; title derives from first message.
    full = client.get(f"/api/assistant/conversations/{conv['id']}",
                      headers=auth(t)).get_json()
    roles = [m["role"] for m in full["messages"]]
    assert roles == ["user", "assistant"]
    assert full["title"].startswith("Draft a SAR")


def test_customer_anchored_conversation(client, tokens):
    t = tokens["analyst@test.io"]
    cid = _cid(client, t, "John Smith")
    conv = client.post("/api/assistant/conversations", headers=auth(t),
                       json={"customer_id": cid}).get_json()
    assert conv["customer_id"] == cid

    r = client.post(f"/api/assistant/conversations/{conv['id']}/messages",
                    headers=auth(t), json={"content": "Why is this high-risk?"})
    assert r.status_code == 201
    assert r.get_json()["reply"]["content"]


def test_empty_message_rejected(client, tokens):
    t = tokens["analyst@test.io"]
    conv = client.post("/api/assistant/conversations", headers=auth(t),
                       json={}).get_json()
    r = client.post(f"/api/assistant/conversations/{conv['id']}/messages",
                    headers=auth(t), json={"content": "   "})
    assert r.status_code == 400


def test_conversations_are_private_to_owner(client, tokens):
    """A conversation is scoped to its creator; another user can't read it."""
    owner = tokens["analyst@test.io"]
    other = tokens["officer@test.io"]
    conv = client.post("/api/assistant/conversations", headers=auth(owner),
                       json={}).get_json()
    r = client.get(f"/api/assistant/conversations/{conv['id']}",
                   headers=auth(other))
    assert r.status_code == 404


def test_cannot_anchor_to_foreign_customer(client, tokens):
    """Anchoring to a customer in another org is rejected."""
    outsider = tokens["outsider@test.io"]
    # Foreign Co lives in the other org; the org's own analyst can't see it,
    # but the outsider can — use the outsider to fetch its id, then confirm our
    # analyst cannot anchor to it.
    foreign_id = _cid(client, outsider, "Foreign Co")
    analyst = tokens["analyst@test.io"]
    r = client.post("/api/assistant/conversations", headers=auth(analyst),
                    json={"customer_id": foreign_id})
    assert r.status_code == 404
