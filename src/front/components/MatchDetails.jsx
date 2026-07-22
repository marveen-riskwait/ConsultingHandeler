import { useState } from "react";

// The evidence behind a screening match, as labelled text — never JSON.
// The fields keep one fixed order whatever the provider, which is what lets an
// analyst put two matches side by side and see at a glance that they are the
// same designation reported twice (same list, same entry id, same programs).
const Row = ({ label, children }) => (
  children ? (
    <div className="md-row">
      <span className="md-label">{label}</span>
      <span className="md-value">{children}</span>
    </div>
  ) : null
);

export const MatchDetails = ({ match }) => {
  const [open, setOpen] = useState(false);
  const d = match.match_data || {};
  const hasDetail = Object.keys(d).length > 0;
  if (!hasDetail) return null;

  return (
    <>
      <button type="button" className="md-toggle" onClick={() => setOpen(!open)}>
        <i className={`fa-solid fa-chevron-${open ? "up" : "down"}`} />{" "}
        {open ? "Hide details" : "Details"}
      </button>
      {open && (
        <div className="md-panel">
          <Row label="Source">
            {match.source}
            {d.list_source ? ` — list ${d.list_source}` : ""}
            {d.external_id ? ` · entry ${d.external_id}` : ""}
          </Row>
          <Row label="Listed name">{match.matched_name}</Row>
          <Row label="Entity type">{d.entity_type}</Row>
          <Row label="Programs">{(d.programs || []).join(", ")}</Row>
          <Row label="Country">{d.country}</Row>
          <Row label="Aliases">{(d.aliases || []).slice(0, 8).join(" · ")}
            {(d.aliases || []).length > 8 ? ` (+${d.aliases.length - 8} more)` : ""}
          </Row>
          <Row label="Remarks">{d.remarks}</Row>
          <Row label="First seen">
            {match.first_detected_at &&
              new Date(match.first_detected_at).toLocaleDateString()}
          </Row>
        </div>
      )}
    </>
  );
};
