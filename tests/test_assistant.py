"""Compliance Copilot: conversation lifecycle, replies, context, isolation.

Runs against the deterministic MockProvider (no ANTHROPIC_API_KEY), so no
network is needed.
"""
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
    finally:
        ai.reset_llm()  # leave the cached provider clean for other tests


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
