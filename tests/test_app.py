import importlib, os, sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["CALLPULSE_ACTIONS_API_KEY"] = "test-key"
Path("test.db").unlink(missing_ok=True)
sys.modules.pop("app", None)
appmod = importlib.import_module("app")
from fastapi.testclient import TestClient

client = TestClient(appmod.app)
H = {"Authorization": "Bearer test-key"}

def campaign(industry="Final Expense"):
    return client.post("/campaigns", json={"name": "Test", "industry": industry}, headers=H).json()
def prospect(cid, **overrides):
    body = {"company_name": "Acme", "email": "Owner@Example.com", "email_verified": True, "score": 65} | overrides
    return client.post(f"/campaigns/{cid}/prospects", json=body, headers=H)

def test_health_auth_launcher_and_industries():
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/campaigns").status_code == 401
    page = client.get("/launcher").text
    industries = client.get("/industries", headers=H).json()["industries"]
    assert "Final Expense" in industries and "Auto Insurance" in industries
    assert all(x in page for x in industries)

def test_campaign_crud_and_validation():
    c = campaign("Auto Insurance")
    assert client.get(f"/campaigns/{c['id']}", headers=H).json()["industry"] == "Auto Insurance"
    assert any(x["id"] == c["id"] for x in client.get("/campaigns", headers=H).json())
    assert client.post("/campaigns", json={"name": "x", "industry": "Unknown"}, headers=H).status_code == 422
    assert client.delete(f"/campaigns/{c['id']}", headers=H).status_code == 204
    assert client.get(f"/campaigns/{c['id']}", headers=H).status_code == 404

def test_schedule_dedupe_and_idempotent_delivery():
    c = campaign(); first = prospect(c["id"]).json(); second = prospect(c["id"]).json()
    assert first["id"] == second["id"]
    assert [x["day"] for x in first["deliveries"]] == [0, 3, 6]
    assert len({x["idempotency_key"] for x in first["deliveries"]}) == 3
    one = client.post("/deliveries/run", headers=H).json(); two = client.post("/deliveries/run", headers=H).json()
    assert one["delivered"] == 1 and two["delivered"] == 0

def test_qualification_and_verified_email_required():
    c = campaign(); prospect(c["id"], email="low@example.com", score=64)
    prospect(c["id"], email="unverified@example.com", email_verified=False, score=100)
    result = client.post("/deliveries/run", headers=H).json()
    assert result["processed"] == 2 and result["delivered"] == 0

def test_each_suppression_event_stops_scheduled_delivery():
    for event in ("reply", "opt_out", "hard_bounce"):
        c = campaign(); p = prospect(c["id"], email=f"{event}@example.com").json()
        updated = client.post(f"/prospects/{p['id']}/events", json={"event": event}, headers=H)
        assert updated.status_code == 200
        assert all(x["status"] == "cancelled" for x in updated.json()["deliveries"])
    assert client.post("/deliveries/run", headers=H).json()["delivered"] == 0

def test_openapi_matches_runtime():
    import yaml
    checked_in = yaml.safe_load(Path("openapi.yaml").read_text())
    runtime = client.get("/openapi.json").json()
    assert checked_in["info"] == runtime["info"]
    documented = {(path, method) for path, item in checked_in["paths"].items() for method in item if method in {"get", "post", "delete"}}
    actual = {(path, method) for path, item in runtime["paths"].items() for method in item if method in {"get", "post", "delete"}}
    assert documented == actual
