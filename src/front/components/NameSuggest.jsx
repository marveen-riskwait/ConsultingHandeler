import { useEffect, useRef, useState } from "react";
import { api } from "../services/api";

// Type-ahead for a customer name. Two things surface while you type:
// the customers you already have (so you stop creating the duplicate) and the
// public watchlists (so a sanctioned name is flagged at entry, not after
// onboarding). Picking an entry fills the field with the canonical spelling.
export const NameSuggest = ({ value, onChange, placeholder }) => {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState({ customers: [], watchlist: [] });
  const [loading, setLoading] = useState(false);
  const ref = useRef(null);
  const seq = useRef(0);
  const justPicked = useRef(false);

  useEffect(() => {
    // Picking a suggestion changes `value`, which would otherwise re-run the
    // search and re-open the list on the entry just chosen.
    if (justPicked.current) { justPicked.current = false; return undefined; }
    const q = (value || "").trim();
    if (q.length < 3) { setData({ customers: [], watchlist: [] }); return undefined; }
    // Debounced, and a reply is dropped once a newer keystroke has fired — an
    // earlier request must never overwrite a later one.
    const mine = ++seq.current;
    setLoading(true);
    const t = setTimeout(() => {
      api.nameSuggestions(q)
        .then((d) => { if (mine === seq.current) { setData(d); setOpen(true); } })
        .catch(() => {})
        .finally(() => { if (mine === seq.current) setLoading(false); });
    }, 220);
    return () => clearTimeout(t);
  }, [value]);

  useEffect(() => {
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const pick = (name) => {
    justPicked.current = true;
    onChange(name);
    setOpen(false);
  };
  const total = data.customers.length + data.watchlist.length;

  return (
    <div className="ns-wrap" ref={ref}>
      <input className="form-control" value={value} required
        placeholder={placeholder} autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => total > 0 && setOpen(true)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)} />
      {loading && <i className="fa-solid fa-circle-notch fa-spin ns-spin" />}

      {open && total > 0 && (
        <div className="ns-pop">
          {data.customers.length > 0 && (
            <>
              <div className="ns-group">Already in your book</div>
              {data.customers.map((c) => (
                <button type="button" key={`c${c.id}`} className="ns-item"
                  onClick={() => pick(c.name)}>
                  <span className={`dotsev ${c.risk_level}`} />
                  <span className="ns-name">{c.name}</span>
                  <span className="ns-meta">
                    {c.customer_type}{c.status === "ARCHIVED" ? " · archived" : ""}
                  </span>
                </button>
              ))}
            </>
          )}
          {data.watchlist.length > 0 && (
            <>
              <div className="ns-group">Public watchlists</div>
              {data.watchlist.map((w, i) => (
                <button type="button" key={`w${i}`} className="ns-item"
                  onClick={() => pick(w.name)}>
                  <i className="fa-solid fa-triangle-exclamation ns-warn" />
                  <span className="ns-name">{w.name}</span>
                  <span className="ns-meta">
                    {w.source}{w.country ? ` · ${w.country}` : ""}
                  </span>
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
};
