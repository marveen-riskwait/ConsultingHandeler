from conftest import auth


def _cid(client, token, name):
    r = client.get("/api/customers", headers=auth(token))
    return next(c["id"] for c in r.get_json() if c["name"] == name)


def test_sanctions_workflow_autostarts_and_approval_gates(client, tokens):
    ta, to = tokens["analyst@test.io"], tokens["officer@test.io"]
    jid = _cid(client, ta, "John Smith")
    client.post(f"/api/customers/{jid}/screen", headers=auth(ta))
    case = next(c for c in client.get("/api/cases?status=OPEN", headers=auth(ta)).get_json()
                if c["case_type"] == "SANCTIONS_MATCH")

    detail = client.get(f"/api/cases/{case['id']}", headers=auth(ta)).get_json()
    wf = detail["workflow"]
    assert wf is not None and len(wf["steps"]) == 4
    assert wf["steps"][0]["status"] == "ACTIVE"

    inst = wf["id"]
    # advance the first three (non-approval) steps
    for _ in range(3):
        r = client.post(f"/api/workflow-instances/{inst}/complete-step",
                        headers=auth(ta))
        assert r.status_code == 200

    # the 4th step needs approval — completing it is blocked
    r = client.post(f"/api/workflow-instances/{inst}/complete-step", headers=auth(ta))
    assert r.status_code == 403

    # analyst cannot approve (needs case.approve); officer can
    r = client.post(f"/api/workflow-instances/{inst}/approve",
                    json={"decision": "APPROVE", "reason": "confirmed"},
                    headers=auth(ta))
    assert r.status_code == 403
    r = client.post(f"/api/workflow-instances/{inst}/approve",
                    json={"decision": "APPROVE", "reason": "confirmed"},
                    headers=auth(to))
    assert r.status_code == 200

    # now the step completes and the workflow finishes
    r = client.post(f"/api/workflow-instances/{inst}/complete-step", headers=auth(ta))
    assert r.get_json()["status"] == "COMPLETED"
