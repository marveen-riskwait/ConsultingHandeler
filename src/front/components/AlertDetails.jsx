// Expandable evidence panel for a ComplianceAlert: renders the triggering
// event's payload (matched name, score, sanction programmes, aliases…) as a
// readable list instead of forcing analysts to open the raw event.
const LABELS = {
  matched_name: "Matched name",
  match_type: "Match type",
  match_score: "Match score",
  source: "List / source",
  list_source: "List",
  external_id: "List entry id",
  entity_type: "Entity type",
  programs: "Sanction programmes",
  aliases: "Aliases (a.k.a.)",
  country: "Country",
  nationality: "Nationality",
  remarks: "Remarks",
  dob: "Date of birth",
  pep_type: "PEP type",
  position: "Position",
  article: "Article",
  category: "Category",
  programme: "Programme",
  status: "Status",
  result_type: "Result type",
  provider: "Provider",
  completeness_pct: "Completeness",
  field: "Field",
  declared: "Declared value",
  found: "Found value",
};

const fmtVal = (v) => {
  if (Array.isArray(v)) return v.join(", ") || "—";
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
};

export const AlertDetails = ({ details }) => {
  if (!details) return <div className="muted" style={{ fontSize: ".8rem" }}>No linked evidence.</div>;
  const payload = details.payload || {};
  // Flatten one nested `data` level (screening matches carry it).
  const rows = [];
  Object.entries(payload).forEach(([k, v]) => {
    if (k === "data" && v && typeof v === "object") {
      Object.entries(v).forEach(([k2, v2]) => rows.push([k2, v2]));
    } else if (k !== "match_id") {
      rows.push([k, v]);
    }
  });

  return (
    <div className="alert-details">
      <div className="meta" style={{ marginBottom: ".3rem" }}>
        Event: <b>{details.event_type}</b>
        {details.detected_at && ` · ${new Date(details.detected_at).toLocaleString()}`}
      </div>
      <dl className="alert-details-grid">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{LABELS[k] || k.replace(/_/g, " ")}</dt>
            <dd>{fmtVal(v)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
};
