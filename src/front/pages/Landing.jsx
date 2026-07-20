import { Link } from "react-router-dom";

// Public landing page (logged-out root). Serif editorial style on the app's
// navy palette — the marketing face of the same design system as the shell.
const FEATURES = [
  { icon: "fa-users", title: "Customer 360 & KYB",
    text: "Individuals and companies with ownership graphs, UBO discovery, directors, addresses and document intelligence in one file." },
  { icon: "fa-magnifying-glass", title: "Sanctions screening, built in",
    text: "Screen every customer against locally-ingested OFAC, UN and EU public lists — no vendor contract required to start." },
  { icon: "fa-gauge-high", title: "Explainable risk",
    text: "Data-driven, versioned methodologies. Every score shows its factors, and every override is recorded with a reason." },
  { icon: "fa-diagram-project", title: "Configurable workflows",
    text: "EDD, sanctions investigations and approvals as step-by-step workflows with senior sign-off gates — defined in data, not code." },
  { icon: "fa-scale-balanced", title: "Regulatory intelligence",
    text: "Map FATF, EU AMLR, AMLA and CSSF obligations to the controls and modules that implement them, and assess changes for impact." },
  { icon: "fa-robot", title: "AI Compliance Copilot",
    text: "Draft SAR narratives, explain a risk rating or summarise a customer file — advisory only, always validated by your MLRO." },
];

const SPINE = ["Data", "Event", "Rule", "Risk", "Requirement",
  "Workflow", "Decision", "Audit"];

export const Landing = () => (
  <div className="ld">
    {/* Top nav */}
    <nav className="ld-nav">
      <span className="ld-brand"><span className="dot" /> Compliance OS</span>
      <div className="ld-nav-actions">
        <Link to="/login" className="btn btn-sm btn-outline-light">Sign in</Link>
        <Link to="/login" className="btn btn-sm ld-btn-accent">Get started</Link>
      </div>
    </nav>

    {/* Hero */}
    <header className="ld-hero">
      <p className="ld-kicker">The compliance operating system</p>
      <h1>
        Run AML &amp; KYC like an <em>operating system</em>,<br />
        not a pile of spreadsheets.
      </h1>
      <p className="ld-sub">
        A modular platform for fintechs and financial institutions: onboarding,
        screening, risk, reviews, workflows and audit — proactive and
        event-driven, so your team only sees the exceptions and the decisions.
      </p>
      <div className="ld-cta-row">
        <Link to="/login" className="btn btn-lg ld-btn-accent">Create your organization</Link>
        <Link to="/login" className="btn btn-lg btn-outline-light">Sign in</Link>
      </div>

      {/* The spine */}
      <div className="ld-spine">
        {SPINE.map((s, i) => (
          <span key={s} className="ld-spine-item">
            {s}{i < SPINE.length - 1 && <i className="fa-solid fa-angle-right" />}
          </span>
        ))}
      </div>
    </header>

    {/* Stats */}
    <section className="ld-stats">
      <div><b>19,000+</b><span>public sanctions records screened locally</span></div>
      <div><b>10 roles</b><span>permission-driven, technical admin ≠ decision maker</span></div>
      <div><b>Every action</b><span>audited — who, what, when, where, why</span></div>
    </section>

    {/* Features */}
    <section className="ld-features">
      <h2>One spine, every compliance module on top</h2>
      <p className="ld-features-sub">
        Each capability plugs into the same event-driven core — a change anywhere
        becomes an event, events trigger rules, rules drive risk and work.
      </p>
      <div className="ld-grid">
        {FEATURES.map((f) => (
          <div className="ld-card" key={f.title}>
            <div className="ld-card-icon"><i className={`fa-solid ${f.icon}`} /></div>
            <h3>{f.title}</h3>
            <p>{f.text}</p>
          </div>
        ))}
      </div>
    </section>

    {/* Bottom CTA */}
    <section className="ld-band">
      <h2>Simpler than the legacy suites.<br />Serious about the controls.</h2>
      <p>Start with the public watchlists and the built-in workflows; connect
        Sumsub, ComplyAdvantage or Companies House when you are ready.</p>
      <Link to="/login" className="btn btn-lg ld-btn-accent">Get started free</Link>
    </section>

    <footer className="ld-foot">
      <span className="ld-brand"><span className="dot" /> Compliance OS</span>
      <span className="muted">Built as a modular compliance platform — KYC · KYB · Screening · Risk · Workflows · Audit</span>
    </footer>
  </div>
);
