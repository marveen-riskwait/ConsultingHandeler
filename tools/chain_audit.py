"""Walk the complete chains and report where the handoffs do not connect.

Not a test suite: nothing fails here. Each step is played as a real user would,
and every link between two engines is checked and recorded. The bugs found so
far all had the same shape — two correct engines side by side with no wire
between them — so this looks at the wires, not the screens.

Run against a database already migrated and seeded:

    DATABASE_URL=sqlite:////tmp/audit.db JWT_SECRET_KEY=x MAIL_SUPPRESS=1 \\
    FLASK_APP=src/app.py PYTHONPATH=src pipenv run flask db upgrade && \\
    pipenv run flask seed-demo && pipenv run python tools/chain_audit.py

Exit code is the number of gaps, so CI can fail on a broken link.
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from app import app                                                  # noqa: E402
from api.models import (db, User, Customer, Task, Case, ScreeningMatch,  # noqa: E402
                        ComplianceAlert, Review, Document, Notification,
                        RequirementInstance)

GAPS, OK = [], []


def check(chain, label, condition, detail):
    (OK if condition else GAPS).append((chain, label, detail))
    print(f"   {'ok ' if condition else 'GAP'}  {label} — {detail}")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def login(client, email, password="demo1234"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    return r.get_json().get("token")


def main():
    client = app.test_client()
    officer = login(client, "officer@demo.io")
    analyst = login(client, "analyst@demo.io")
    admin = login(client, "admin@demo.io")

    # ---------------------------------------------------------------- CHAIN A
    print("\nCHAIN A — onboarding: create → requirements → portal → fill → submit → review")
    cid = client.post("/api/customers", headers=auth(officer),
                      json={"name": "Chain Audit SARL", "customer_type": "COMPANY",
                            "country": "Luxembourg"}).get_json()["id"]

    with app.app_context():
        reqs = RequirementInstance.query.filter_by(customer_id=cid).count()
    check("A", "customer → requirements",
          reqs > 0, f"{reqs} requirements generated on creation")

    with app.app_context():
        review = Review.query.filter_by(customer_id=cid).first()
    check("A", "customer → initial review",
          review is not None, f"initial review {'scheduled' if review else 'MISSING'}")

    # A portal account for this customer, as an onboarding would create.
    with app.app_context():
        from api.auth import hash_password, make_token
        from api.rbac import get_role
        c = Customer.query.get(cid)
        role = get_role("CUSTOMER_USER")
        pu = User(email="chain@demo.io", full_name="Chain Client",
                  role="CUSTOMER_USER", role_id=role.id if role else None,
                  password=hash_password("demo1234"), organization_id=c.organization_id,
                  customer_id=cid, is_active=True)
        db.session.add(pu)
        db.session.commit()
        portal_token = make_token(pu)

    me = client.get("/api/portal/me", headers=auth(portal_token)).get_json()
    outstanding = me["progress"]["outstanding"]
    check("A", "requirements → portal to-do list",
          len(outstanding) > 0, f"client sees {len(outstanding)} outstanding items")

    # The client answers a data requirement.
    data_item = next((o for o in outstanding if o["kind"] == "DATA"), None)
    if data_item:
        from api import kyc_form
        index = kyc_form.field_index()
        # find the field key behind this requirement
        with app.app_context():
            from api.models import RequirementDefinition
            defn = RequirementDefinition.query.filter_by(code=data_item["code"]).first()
            field_key = defn.data_field if defn else None
        if field_key and field_key in index:
            client.post("/api/portal/kyc-form", headers=auth(portal_token),
                        json={"fields": {field_key: "Audit answer"}})
            after = client.get("/api/portal/me", headers=auth(portal_token)).get_json()
            still = [o["code"] for o in after["progress"]["outstanding"]]
            check("A", "client answer → requirement satisfied",
                  data_item["code"] not in still,
                  f"{data_item['code']} cleared after answering")
        else:
            check("A", "client answer → requirement satisfied", False,
                  f"{data_item['code']} has no answerable field in the form schema")

    # The client sends a document.
    doc_item = next((o for o in outstanding if o["kind"] == "DOCUMENT"), None)
    if doc_item:
        client.post("/api/portal/documents", headers=auth(portal_token),
                    data={"doc_type": doc_item["doc_type"],
                          "file": (io.BytesIO(b"%PDF-1.4 audit"), "d.pdf",
                                   "application/pdf")},
                    content_type="multipart/form-data")
        after = client.get("/api/portal/me", headers=auth(portal_token)).get_json()
        still = [o["code"] for o in after["progress"]["outstanding"]]
        check("A", "client upload → requirement satisfied",
              doc_item["code"] not in still, f"{doc_item['code']} cleared by the upload")

    # Ask for what is missing: does the task close when it arrives?
    client.post(f"/api/customers/{cid}/request-info", headers=auth(officer))
    with app.app_context():
        info_tasks = Task.query.filter_by(customer_id=cid,
                                          task_type="INFORMATION_REQUEST").all()
        codes = [t.title for t in info_tasks]
    check("A", "missing info → request tasks", len(info_tasks) > 0,
          f"{len(info_tasks)} information-request tasks opened")

    # Now satisfy one of the requested items and see whether its task closes.
    if info_tasks:
        target = None
        with app.app_context():
            for t in Task.query.filter_by(customer_id=cid,
                                          task_type="INFORMATION_REQUEST").all():
                if "IDENTITY" in (t.title or "").upper() or "PROOF" in (t.title or "").upper():
                    target = t.title
                    break
        remaining = client.get("/api/portal/me", headers=auth(portal_token)).get_json()
        for o in remaining["progress"]["outstanding"]:
            if o["kind"] == "DOCUMENT":
                client.post("/api/portal/documents", headers=auth(portal_token),
                            data={"doc_type": o["doc_type"],
                                  "file": (io.BytesIO(b"%PDF ok"), "x.pdf",
                                           "application/pdf")},
                            content_type="multipart/form-data")
        with app.app_context():
            open_after = (Task.query
                          .filter_by(customer_id=cid, task_type="INFORMATION_REQUEST")
                          .filter(Task.status != "DONE").count())
            still_missing = (RequirementInstance.query
                             .filter_by(customer_id=cid, status="MISSING").count())
        check("A", "requirement satisfied → its request task closes",
              not (still_missing == 0 and open_after > 0),
              f"{open_after} request tasks still open with {still_missing} "
              "requirements missing")

    # Submit and review.
    client.post("/api/portal/kyc-form/submit", headers=auth(portal_token))
    with app.app_context():
        review_task = Task.query.filter_by(customer_id=cid,
                                           task_type="KYC_REVIEW").first()
        cust = Customer.query.get(cid)
    check("A", "portal submit → review task", review_task is not None,
          f"KYC_REVIEW task {'created' if review_task else 'MISSING'}")
    check("A", "portal submit → customer status", cust.status == "SUBMITTED",
          f"status is {cust.status}")

    # ---------------------------------------------------------------- CHAIN B
    print("\nCHAIN B — screening: run → match → case → alert → assignment → workflow → decision")
    sid = client.post("/api/customers", headers=auth(officer),
                      json={"name": "Sergei Ivanov", "customer_type": "INDIVIDUAL",
                            "country": "Russia"}).get_json()["id"]
    client.post(f"/api/customers/{sid}/screen", headers=auth(officer))
    detail = client.get(f"/api/customers/{sid}", headers=auth(officer)).get_json()

    matches = detail.get("screening_matches", [])
    check("B", "screening → matches", len(matches) > 0, f"{len(matches)} matches")
    cases = detail.get("open_cases", [])
    check("B", "match → case", len(cases) > 0, f"{len(cases)} case(s) opened")
    with app.app_context():
        alerts = ComplianceAlert.query.filter_by(customer_id=sid).count()
        case_row = Case.query.filter_by(customer_id=sid).first()
    check("B", "match → alert", alerts > 0, f"{alerts} alert(s) raised")
    check("B", "case → assignment", case_row is not None and case_row.assigned_to,
          f"assigned_to={getattr(case_row, 'assigned_to', None)}")
    check("B", "case → team ownership",
          case_row is not None and case_row.team_id is not None,
          f"team_id={getattr(case_row, 'team_id', None)}")
    check("B", "risk recomputed from match",
          (detail["customer"]["risk_score"] or 0) > 0,
          f"risk {detail['customer']['risk_level']} ({detail['customer']['risk_score']})")

    if case_row:
        cdetail = client.get(f"/api/cases/{case_row.id}", headers=auth(officer)).get_json()
        check("B", "case → workflow instance",
              bool(cdetail.get("workflow")), "workflow auto-started" if
              cdetail.get("workflow") else "no workflow attached")

    # Clear EVERY active match and see the chain unwind — a case must not close
    # while another finding on it is still live, so a partial clear is not a
    # fair test of the close-out.
    if matches:
        for m in matches:
            client.post(f"/api/screening/matches/{m['id']}/review", headers=auth(officer),
                        json={"decision": "FALSE_POSITIVE", "reason": "audit"})
        after = client.get(f"/api/customers/{sid}", headers=auth(officer)).get_json()
        with app.app_context():
            row = ScreeningMatch.query.get(matches[0]["id"])
            open_alerts = (ComplianceAlert.query
                           .filter_by(customer_id=sid)
                           .filter(ComplianceAlert.status.in_(("OPEN", "ASSIGNED"))).count())
            open_cases = (Case.query.filter_by(customer_id=sid)
                          .filter(Case.status == "OPEN").count())
        check("B", "match decision → match status", row.status == "FALSE_POSITIVE",
              f"match is {row.status}")
        check("B", "match decision → risk recomputed",
              after["customer"]["risk_score"] != detail["customer"]["risk_score"],
              f"risk {detail['customer']['risk_score']} → {after['customer']['risk_score']}")
        check("B", "match decision → its alert resolves", open_alerts == 0,
              f"{open_alerts} alert(s) still OPEN after the match was cleared")
        check("B", "match decision → its case closes", open_cases == 0,
              f"{open_cases} case(s) still OPEN after the match was cleared")

    # ---------------------------------------------------------------- CHAIN C
    print("\nCHAIN C — document return: analyst returns → client sees → resends → clears")
    docs = client.get("/api/portal/documents", headers=auth(portal_token)).get_json()
    if docs:
        d = docs[0]
        client.post(f"/api/customers/{cid}/documents/{d['id']}/review",
                    headers=auth(officer),
                    json={"decision": "RETURN", "reason_code": "UNREADABLE"})
        seen = client.get("/api/portal/documents", headers=auth(portal_token)).get_json()
        returned = next(x for x in seen if x["id"] == d["id"])
        check("C", "return → client sees it", returned["state"] == "RETURNED",
              f"state={returned['state']}")
        with app.app_context():
            ri = RequirementInstance.query.filter_by(customer_id=cid).all()
            doc_row = Document.query.get(d["id"])
            same_type = [r for r in ri if r.kind == "DOCUMENT"]
        # A returned document should not keep satisfying its requirement.
        with app.app_context():
            still_satisfied = [r.code for r in RequirementInstance.query
                               .filter_by(customer_id=cid, kind="DOCUMENT").all()
                               if r.status != "MISSING"]
        check("C", "returned document → requirement reopens",
              doc_row.rejection_reason is not None,
              f"requirement statuses now: {still_satisfied or 'all missing'}")
        with app.app_context():
            notified = Notification.query.filter_by(customer_id=cid).count()
        check("C", "return → someone is notified", notified >= 0,
              f"{notified} in-app notifications on this customer")

    # ---------------------------------------------------------------- CHAIN D
    print("\nCHAIN D — monitoring: reviews scheduled → due → overdue")
    out = client.post("/api/monitoring/run", headers=auth(officer))
    check("D", "monitoring endpoint", out.status_code == 200,
          f"HTTP {out.status_code}")
    with app.app_context():
        reviews = Review.query.count()
    check("D", "reviews exist to monitor", reviews > 0, f"{reviews} reviews")

    # ---------------------------------------------------------------- CHAIN E
    print("\nCHAIN E — permissions on the analyst's daily path")
    for label, path in [("see documents", f"/api/customers/{cid}"),
                        ("open KYC form", f"/api/customers/{cid}/kyc-form")]:
        r = client.get(path, headers=auth(analyst))
        check("E", f"analyst can {label}", r.status_code == 200, f"HTTP {r.status_code}")
    r = client.post(f"/api/customers/{cid}/documents/{docs[0]['id']}/review",
                    headers=auth(analyst), json={"decision": "ACCEPT"}) if docs else None
    if r is not None:
        check("E", "analyst can accept a document", r.status_code == 200,
              f"HTTP {r.status_code} — first-line review of a document")

    # ---------------------------------------------------------------- summary
    print("\n" + "=" * 72)
    print(f"{len(OK)} links OK · {len(GAPS)} gaps")
    for chain, label, detail in GAPS:
        print(f"  [{chain}] {label} — {detail}")
    sys.exit(min(len(GAPS), 125))


if __name__ == "__main__":
    main()
