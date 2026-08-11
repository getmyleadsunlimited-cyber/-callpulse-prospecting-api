import os
os.environ["DATABASE_URL"] = "sqlite:///./test_callpulse.db"
os.environ["CALLPULSE_ACTIONS_API_KEY"] = "test"
import pytest
from fastapi.testclient import TestClient
from app import Base, app, engine
@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine); yield
@pytest.fixture
def client(): return TestClient(app, headers={"Authorization":"Bearer test"})
@pytest.fixture
def campaign(client):
    r=client.post("/campaigns",json={"name":"Houston Roofing","industry":"Roofing","geography":"Houston, TX","start_date":"2026-08-11","daily_first_touch_limit":25,"minimum_score":65,"allowed_priority_levels":["A","B"]})
    assert r.status_code==200
    cid=r.json()["id"]; client.post(f"/campaigns/{cid}/start"); return cid
@pytest.fixture
def prospect_payload():
    return {"company_name":"Acme Roof","website":"https://acme.test","industry":"Roofing","score":75,"priority":"A","why_now":"Site opportunity","ai_recovery_opportunity":"Visitor recovery","verified_facts":["Offers roof inspections"],"verified_contact":"owner@acme.test","email_verified":True}
