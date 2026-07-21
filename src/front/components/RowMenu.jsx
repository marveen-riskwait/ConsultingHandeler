import { useEffect, useRef, useState } from "react";

// A quiet overflow menu for secondary and destructive row actions, so they stop
// sitting one pixel away from the action you actually meant to click.
// Self-contained on purpose: no Bootstrap data-attributes, no global state.
export const RowMenu = ({ items, label = "More actions" }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const visible = items.filter(Boolean);
  if (visible.length === 0) return null;

  return (
    <div className="row-menu" ref={ref}>
      <button type="button" className="row-menu-trigger" title={label}
        aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen((o) => !o)}>
        <i className="fa-solid fa-ellipsis" />
      </button>
      {open && (
        <div className="row-menu-pop" role="menu">
          {visible.map((item) => (
            <button key={item.label} type="button" role="menuitem"
              className={"row-menu-item" + (item.danger ? " danger" : "")}
              onClick={() => { setOpen(false); item.onClick(); }}>
              {item.icon && <i className={item.icon} />} {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};
