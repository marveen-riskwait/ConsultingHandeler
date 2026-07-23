"""Assignment candidates (/users/assignable): team-scoped when the caller
belongs to a team, org-wide staff otherwise; portal accounts and other
organizations never appear."""
from conftest import auth


def test_teamless_assigner_sees_whole_staff(client, tokens):
    r = client.get("/api/users/assignable",
                   headers=auth(tokens["officer@test.io"]))
    assert r.status_code == 200
    emails = {u["email"] for u in r.get_json()}
    assert {"analyst@test.io", "officer@test.io",
            "manager@test.io", "admin@test.io"} <= emails
    # Tenant isolation: staff of another organization is never offered.
    assert "outsider@test.io" not in emails


def test_team_member_sees_only_team_mates(client, tokens, app):
    from api.models import db, User, Team, Department, TeamMembership
    with app.app_context():
        officer = User.query.filter_by(email="officer@test.io").first()
        dept = Department(organization_id=officer.organization_id,
                          name="Assignable Test Dept")
        db.session.add(dept)
        db.session.flush()
        team = Team(organization_id=officer.organization_id,
                    department_id=dept.id, name="Assignable Test Team")
        db.session.add(team)
        db.session.flush()
        for email in ("officer@test.io", "analyst@test.io"):
            u = User.query.filter_by(email=email).first()
            db.session.add(TeamMembership(team_id=team.id, user_id=u.id))
        db.session.commit()

    r = client.get("/api/users/assignable",
                   headers=auth(tokens["officer@test.io"]))
    emails = {u["email"] for u in r.get_json()}
    assert emails == {"officer@test.io", "analyst@test.io"}
