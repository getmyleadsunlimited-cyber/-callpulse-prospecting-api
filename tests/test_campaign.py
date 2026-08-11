import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("CALLPULSE_ACTIONS_API_KEY", "secret")
    import app
    importlib.reload(app)
    return TestClient(app.app)


def headers(): return {"Authorization": "Bearer secret"}


def prospect(email="verified@example.com", verified=True):
    return {"company_name": "Example Agency", "website": "https://example.com", "industry": "Final Expense", "score": 91,
            "why_now": "Missed calls", "ai_recovery_opportunity": "Immediate callbacks", "verified_email": email,
            "email_verified": verified, "opening_message": "Hello"}


def test_auth_fails_closed(client):
    assert client.get("/prospects").status_code == 401


def test_verified_email_and_industry_are_required(client):
    assert client.post("/prospects", json=prospect(verified=False), headers=headers()).status_code == 422
    body = prospect(); body["industry"] = "Unknown"
    assert client.post("/prospects", json=body, headers=headers()).status_code == 422


def test_campaign_days_idempotency_launcher_and_reply_stop(client):
    p = client.post("/prospects", json=prospect(), headers=headers()).json()
    start = datetime.now(timezone.utc) - timedelta(days=4)
    payload = {"start_at": start.isoformat(), "idempotency_key": "request-123"}
    first = client.post(f"/prospects/{p['id']}/campaigns", json=payload, headers=headers())
    second = client.post(f"/prospects/{p['id']}/campaigns", json=payload, headers=headers())
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert [x["day"] for x in first.json()["touches"]] == [0, 3, 6]
    run = client.post("/launcher/run", params={"now_at": datetime.now(timezone.utc).isoformat()}, headers=headers()).json()
    assert run == {"processed": 2, "sent_or_simulated": 2, "suppressed": 0, "failed": 0, "dry_run": True}
    assert client.post(f"/prospects/{p['id']}/reply", json={"reply_text": "Stop", "intent": "not_interested"}, headers=headers()).status_code == 200


def test_suppression_blocks_launch(client):
    p = client.post("/prospects", json=prospect("blocked@example.com"), headers=headers()).json()
    assert client.post("/suppressions", json={"email": "BLOCKED@example.com", "reason": "opt-out"}, headers=headers()).status_code == 201
    response = client.post(f"/prospects/{p['id']}/campaigns", json={"idempotency_key": "request-456"}, headers=headers())
    assert response.status_code == 409
