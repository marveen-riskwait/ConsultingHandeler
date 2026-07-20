import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../services/api";

// Compliance Copilot — an advisory chat surface. It never takes actions in the
// platform; every reply carries the "validate with your MLRO" reminder.
export const Assistant = () => {
  const [params] = useSearchParams();
  const anchorCustomer = params.get("customer");

  const [meta, setMeta] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [active, setActive] = useState(null);      // full conversation w/ messages
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);

  const loadConversations = () => api.conversations().then(setConversations);

  useEffect(() => {
    api.assistantMeta().then(setMeta).catch((e) => setError(e.message));
    loadConversations().catch((e) => setError(e.message));
  }, []);

  // If arriving with ?customer=<id>, open a fresh conversation anchored to it.
  useEffect(() => {
    if (anchorCustomer) {
      startConversation(Number(anchorCustomer));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorCustomer]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [active, sending]);

  const openConversation = (id) =>
    api.conversation(id).then(setActive).catch((e) => setError(e.message));

  const startConversation = async (customerId) => {
    try {
      const conv = await api.createConversation(
        customerId ? { customer_id: customerId } : {});
      setActive(conv);
      loadConversations();
    } catch (e) { setError(e.message); }
  };

  const send = async (text) => {
    const content = (text ?? draft).trim();
    if (!content || sending) return;
    setError(null);
    let conv = active;
    if (!conv) {
      conv = await api.createConversation({});
      setActive(conv);
    }
    // Optimistic user bubble.
    setActive((c) => ({ ...c, messages: [...(c?.messages || []),
      { id: `tmp-${Date.now()}`, role: "user", content }] }));
    setDraft("");
    setSending(true);
    try {
      await api.sendAssistantMessage(conv.id, content);
      await openConversation(conv.id);
      loadConversations();
    } catch (e) {
      setError(e.message);
    } finally {
      setSending(false);
    }
  };

  const suggestions = meta?.suggested_prompts || [];
  const messages = active?.messages || [];

  return (
    <>
      <div className="cop-head">
        <div>
          <h3 style={{ margin: 0 }}>Compliance Copilot</h3>
          <p className="muted" style={{ margin: ".15rem 0 0" }}>
            AI assistance for drafting, explaining and summarising —{" "}
            {meta ? (meta.provider === "claude"
              ? <span className="cop-live">Claude connected</span>
              : <span className="cop-demo">Demo mode</span>) : "…"}
          </p>
        </div>
        <button className="btn btn-co btn-sm" onClick={() => startConversation()}>
          <i className="fa-solid fa-plus" /> New chat
        </button>
      </div>

      <div className="cop-layout">
        <aside className="co-card cop-list">
          <div className="section-title">Conversations</div>
          {conversations.length === 0 && <div className="empty" style={{ padding: ".75rem" }}>No conversations yet.</div>}
          {conversations.map((c) => (
            <button key={c.id}
              className={"cop-list-item" + (active?.id === c.id ? " active" : "")}
              onClick={() => openConversation(c.id)}>
              <i className={`fa-solid ${c.customer_id ? "fa-user-tag" : "fa-comment"}`} />
              <span className="cop-list-title">{c.title}</span>
            </button>
          ))}
        </aside>

        <section className="co-card cop-thread">
          {error && <div className="alert alert-danger py-2">{error}</div>}

          <div className="cop-messages" ref={scrollRef}>
            {messages.length === 0 && (
              <div className="cop-welcome">
                <div className="cop-orb"><i className="fa-solid fa-robot" /></div>
                <p>Ask me to draft a SAR narrative, explain a risk rating, or summarise a customer file.</p>
                <div className="cop-suggestions">
                  {suggestions.map((s) => (
                    <button key={s} className="cop-chip" onClick={() => send(s)}>{s}</button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`cop-msg cop-${m.role}`}>
                <div className="cop-bubble">{m.content}</div>
              </div>
            ))}
            {sending && (
              <div className="cop-msg cop-assistant">
                <div className="cop-bubble cop-typing">Thinking…</div>
              </div>
            )}
          </div>

          <form className="cop-composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
            <input className="form-control" placeholder="Message the Copilot…"
              value={draft} onChange={(e) => setDraft(e.target.value)} disabled={sending} />
            <button className="btn btn-co" type="submit" disabled={sending || !draft.trim()}>
              <i className="fa-solid fa-paper-plane" />
            </button>
          </form>
          <p className="cop-disclaimer">
            The Copilot is advisory. Always validate its output with your MLRO before acting.
          </p>
        </section>
      </div>
    </>
  );
};
