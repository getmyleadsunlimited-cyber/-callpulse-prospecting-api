import importlib
import json
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

VERTICALS = [
    "eCommerce", "Roofing", "HVAC", "Dental", "Garage Door Repair", "Plumbing",
    "Emergency Towing", "Water Restoration", "Mold Remediation", "Pest Control",
    "Electrical", "Foundation Repair", "Tree Service", "Pool Service",
    "Landscaping / Lawn Care", "Med Spa", "Final Expense", "Auto Insurance",
]


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


def test_missing_token_returns_401(client):
    assert client.get("/prospects").status_code == 401


def test_wrong_token_returns_401(client):
    response = client.get("/prospects", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_correct_token_allows_protected_endpoint(client):
    response = client.get("/prospects", headers=headers())
    assert response.status_code == 200
    assert response.json() == []


def test_openapi_uses_http_bearer_security_for_protected_endpoints(client):
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }

    protected_operations = {
        ("/industries", "get"),
        ("/prospects", "get"),
        ("/prospects", "post"),
        ("/prospects/{prospect_id}/campaigns", "post"),
        ("/prospects/{prospect_id}/campaigns", "get"),
        ("/campaigns/{campaign_id}/deliveries", "get"),
        ("/campaigns/{campaign_id}/authorize-live", "post"),
        ("/campaigns/{campaign_id}/safety", "get"),
        ("/campaigns/{campaign_id}/canary-execute", "post"),
        ("/deliveries/{delivery_id}/execution", "get"),
        ("/deliveries/{delivery_id}/canary-preflight", "get"),
        ("/launcher/run", "post"),
        ("/suppressions", "post"),
        ("/prospects/{prospect_id}/reply", "post"),
        ("/prospects/{prospect_id}/conversion", "post"),
    }
    for path, method in protected_operations:
        assert schema["paths"][path][method]["security"] == [{"HTTPBearer": []}]

    assert "security" not in schema["paths"]["/health"]["get"]
    assert "security" not in schema["paths"]["/launcher"]["get"]


def test_launcher_exists_and_returns_campaign_ui(client):
    response = client.get("/launcher")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "CallPulse Campaign Launcher" in response.text
    for vertical in VERTICALS:
        assert f'data-industry="{vertical}"' in response.text
    assert "Insurance" in response.text
    assert 'value="Houston, TX"' in response.text
    assert "Campaign summary" in response.text
    assert "Day 0 / Day 3 / Day 6" in response.text
    assert "min=\"65\"" in response.text
    # `window.location` is a browser global; redeclaring it can prevent the whole
    # classic script from initializing in production browsers.
    assert "const location =" not in response.text
    assert "const locationInput =" in response.text
    assert "industry: launcherState.selectedIndustry" in response.text
    assert "`/prospects/${prospect.id}/campaigns`" in response.text


@pytest.mark.parametrize("industry", ["Roofing", "HVAC"])
def test_launcher_click_selects_vertical_and_sends_it_in_prospect_payload(client, industry):
    """Exercise the inline browser code without replacing it with a test implementation."""
    html = client.get("/launcher").text
    script = re.search(r"<script>(.*?)</script>", html, re.DOTALL).group(1)
    runner = f"""
const vm = require('node:vm');
class Element {{
  constructor(id, value = '') {{
    this.id = id; this.value = value; this.checked = true; this.textContent = '';
    this.dataset = {{}}; this.attributes = {{}}; this.listeners = {{}};
    this.classList = {{
      values: new Set(),
      add: value => this.classList.values.add(value),
      remove: value => this.classList.values.delete(value),
      contains: value => this.classList.values.has(value)
    }};
  }}
  addEventListener(type, callback) {{ this.listeners[type] = callback; }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
  async trigger(type) {{ return this.listeners[type]({{preventDefault() {{}}}}); }}
}}
const ids = {{
  'campaign-form': new Element('campaign-form'), result: new Element('result'),
  location: new Element('location', 'Houston, TX'), 'summary-location': new Element('summary-location'),
  industry: new Element('industry'), 'summary-industry': new Element('summary-industry'),
  message: new Element('message'), 'api-key': new Element('api-key', 'secret'),
  company: new Element('company', 'Example Agency'), website: new Element('website', 'https://example.com'),
  score: new Element('score', '91'), why: new Element('why', 'Missed calls'),
  opportunity: new Element('opportunity', 'Immediate callbacks'),
  email: new Element('email', '{industry.lower()}@example.com'), verified: new Element('verified')
}};
const buttons = ['Roofing', 'HVAC'].map(name => {{
  const button = new Element(name); button.dataset.industry = name; return button;
}});
const requests = [];
const context = {{
  document: {{
    querySelector: selector => ids[selector.slice(1)],
    querySelectorAll: selector => selector === '.industry' ? buttons : []
  }},
  fetch: async (path, options) => {{
    requests.push({{path, body: options.body && JSON.parse(options.body)}});
    return {{ok: true, status: 201, json: async () => path === '/prospects' ? {{id: 17}} : {{id: 23}}}};
  }},
  crypto: {{randomUUID: () => '12345678-1234-1234-1234-123456789abc'}},
  console
}};
Object.defineProperty(context, 'location', {{value: {{href: 'https://example.com/launcher'}}, configurable: false}});
vm.createContext(context);
const source = {json.dumps(script)} + `\n;globalThis.testDone = (async () => {{
  const selected = buttons.find(button => button.dataset.industry === {json.dumps(industry)});
  await selected.trigger('click');
  if (!selected.classList.contains('selected')) throw new Error('button is not visibly selected');
  if (selected.attributes['aria-pressed'] !== 'true') throw new Error('aria selection state not updated');
  if (ids.industry.value !== {json.dumps(industry)}) throw new Error('hidden industry state not updated');
  if (ids['summary-industry'].textContent !== {json.dumps(industry)}) throw new Error('summary not updated');
  if (!ids.message.value.includes({json.dumps('inspection and replacement visitors' if industry == 'Roofing' else 'AC repair and replacement visitors')})) throw new Error('wrong opening message');
  await ids['campaign-form'].trigger('submit');
  if (requests[0].path !== '/prospects' || requests[0].body.industry !== {json.dumps(industry)}) throw new Error('wrong prospect payload');
}})();`;
vm.runInContext(source, context);
context.testDone.then(() => process.stdout.write(JSON.stringify(requests[0].body))).catch(error => {{ console.error(error); process.exitCode = 1; }});
"""
    completed = subprocess.run(["node", "-e", runner], capture_output=True, text=True, check=True)
    assert json.loads(completed.stdout)["industry"] == industry


def test_verified_email_and_industry_are_required(client):
    assert client.post("/prospects", json=prospect(verified=False), headers=headers()).status_code == 422
    body = prospect(); body["industry"] = "Unknown"
    assert client.post("/prospects", json=body, headers=headers()).status_code == 422


@pytest.mark.parametrize("industry", VERTICALS)
def test_selected_industry_is_accepted_and_stored(client, industry):
    body = prospect(f"{VERTICALS.index(industry)}@example.com")
    body["industry"] = industry
    body["location"] = "Houston, TX"
    response = client.post("/prospects", json=body, headers=headers())
    assert response.status_code == 201
    assert response.json()["industry"] == industry
    assert response.json()["location"] == "Houston, TX"


def test_score_below_65_is_rejected(client):
    body = prospect()
    body["score"] = 64
    assert client.post("/prospects", json=body, headers=headers()).status_code == 422


def test_industry_helper_becomes_day_zero_message(client):
    body = prospect()
    body.update(industry="Roofing", opening_message=None)
    p = client.post("/prospects", json=body, headers=headers()).json()
    campaign = client.post(f"/prospects/{p['id']}/campaigns", json={"idempotency_key": "roofing-123"}, headers=headers())
    assert campaign.status_code == 201
    assert "inspection and replacement visitors" in campaign.json()["touches"][0]["message"]


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


def test_campaign_inspection_is_authenticated_and_missing_resources_are_404(client):
    assert client.get("/prospects/1/campaigns").status_code == 401
    assert client.get("/campaigns/1/deliveries").status_code == 401
    assert client.get("/prospects/999/campaigns", headers=headers()).status_code == 404
    assert client.get("/campaigns/999/deliveries", headers=headers()).status_code == 404


def test_inspection_returns_three_dry_run_deliveries_without_changing_state(client):
    import app

    p = client.post("/prospects", json=prospect(), headers=headers()).json()
    launched = client.post(
        f"/prospects/{p['id']}/campaigns",
        json={"start_at": datetime.now(timezone.utc).isoformat(), "idempotency_key": "inspect-123"},
        headers=headers(),
    ).json()
    with app.engine.connect() as connection:
        before_campaign = connection.exec_driver_sql("SELECT * FROM campaigns").mappings().all()
        before_touches = connection.exec_driver_sql("SELECT * FROM campaign_touches ORDER BY day").mappings().all()

    campaigns = client.get(f"/prospects/{p['id']}/campaigns", headers=headers())
    deliveries = client.get(f"/campaigns/{launched['id']}/deliveries", headers=headers())

    assert campaigns.status_code == deliveries.status_code == 200
    assert len(campaigns.json()) == 1
    assert campaigns.json()[0]["industry"] == "Final Expense"
    assert campaigns.json()[0]["dry_run"] is True
    assert campaigns.json()[0]["stopped"] is False
    assert [state["day"] for state in campaigns.json()[0]["current_sequence_state"]] == [0, 3, 6]
    assert len(deliveries.json()) == 3
    assert [delivery["sequence_day"] for delivery in deliveries.json()] == [0, 3, 6]
    assert all(delivery["dry_run"] is True for delivery in deliveries.json())
    assert all(delivery["status"] == "scheduled" for delivery in deliveries.json())

    with app.engine.connect() as connection:
        after_campaign = connection.exec_driver_sql("SELECT * FROM campaigns").mappings().all()
        after_touches = connection.exec_driver_sql("SELECT * FROM campaign_touches ORDER BY day").mappings().all()
    assert before_campaign == after_campaign
    assert before_touches == after_touches


def test_suppression_blocks_launch(client):
    p = client.post("/prospects", json=prospect("blocked@example.com"), headers=headers()).json()
    assert client.post("/suppressions", json={"email": "BLOCKED@example.com", "reason": "opt-out"}, headers=headers()).status_code == 201
    response = client.post(f"/prospects/{p['id']}/campaigns", json={"idempotency_key": "request-456"}, headers=headers())
    assert response.status_code == 409


def test_migration_schema_check_covers_every_model_column(client):
    """A PostgreSQL startup must fail if any model column was not migrated."""
    import app
    from migrate import schema_drift

    assert schema_drift(app.engine, app.Base.metadata) == []
    database_columns = {
        table.name: {column["name"] for column in inspect(app.engine).get_columns(table.name)}
        for table in app.Base.metadata.sorted_tables
    }
    expected_columns = {
        table.name: {column.name for column in table.columns}
        for table in app.Base.metadata.sorted_tables
    }
    assert database_columns == expected_columns

    with app.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE prospects DROP COLUMN location")
    assert schema_drift(app.engine, app.Base.metadata) == ["missing column: prospects.location"]


def test_inspection_fields_match_the_existing_persisted_campaign_schema(client):
    """Document which inspection facts are durable instead of inventing columns."""
    import app

    campaign_columns = {column.name for column in app.Campaign.__table__.columns}
    touch_columns = {column.name for column in app.Touch.__table__.columns}
    assert campaign_columns == {
        "id", "prospect_id", "status", "starts_at", "ends_at", "dry_run",
        "live_authorized", "live_authorized_at", "live_authorized_by",
    }
    assert touch_columns == {
        "id", "campaign_id", "day", "scheduled_at", "status", "message", "idempotency_key", "sent_at",
        "dry_run", "skipped", "cancelled", "cancellation_or_skip_reason",
        "execution_status", "execution_started_at", "execution_completed_at", "provider_name",
        "provider_message_id", "last_execution_error", "execution_attempt_count",
    }
    assert {"created_at", "stop_reason"}.isdisjoint(campaign_columns)
    assert {"cancellation_reason", "skip_reason"}.isdisjoint(touch_columns)


def test_render_start_applies_idempotent_location_migration_with_dry_run_enabled():
    render_config = Path("render.yaml").read_text(encoding="utf-8")
    location_migration = Path("migrations/003_ensure_prospect_location.sql").read_text(encoding="utf-8")

    assert "startCommand: python migrate.py && uvicorn app:app" in render_config
    assert "- key: CALLPULSE_DRY_RUN\n        value: true" in render_config
    assert "ADD COLUMN IF NOT EXISTS location" in location_migration
    assert "DROP TABLE" not in location_migration.upper()


def launch(client, email="live@example.com", start=None):
    created = client.post("/prospects", json=prospect(email), headers=headers()).json()
    response = client.post(
        f"/prospects/{created['id']}/campaigns",
        json={"idempotency_key": f"launch-{email}", "start_at": (start or datetime.now(timezone.utc)).isoformat()},
        headers=headers(),
    )
    assert response.status_code == 201
    return created, response.json()


def authorize(client, campaign_id, authorized_by="safety officer", confirmation="AUTHORIZE LIVE OUTREACH"):
    return client.post(
        f"/campaigns/{campaign_id}/authorize-live",
        json={"authorized_by": authorized_by, "confirmation": confirmation}, headers=headers(),
    )


def execute_canary(client, campaign, delivery_id=None, authorized_by="operator",
                   confirmation="EXECUTE ONE CANARY DELIVERY"):
    return client.post(f"/campaigns/{campaign['id']}/canary-execute", json={
        "authorized_by": authorized_by, "confirmation": confirmation,
        "delivery_id": delivery_id or campaign["touches"][0]["id"],
    }, headers=headers())


def configure_mock(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "EMAIL_PROVIDER_NAME", "mock")
    monkeypatch.setattr(app_module, "EMAIL_FROM", "approved-sender@example.com")
    from email_providers import DeterministicMockEmailProvider
    DeterministicMockEmailProvider.calls.clear()
    return DeterministicMockEmailProvider


def test_canary_auth_body_and_campaign_safety_fail_closed(client, monkeypatch):
    import app
    _, campaign = launch(client, "canary-blocked@example.com")
    configure_mock(app, monkeypatch)
    url = f"/campaigns/{campaign['id']}/canary-execute"
    valid = {"authorized_by": "operator", "confirmation": "EXECUTE ONE CANARY DELIVERY",
             "delivery_id": campaign["touches"][0]["id"]}
    assert client.post(url, json=valid).status_code == 401
    assert client.post(url, json={**valid, "confirmation": "yes"}, headers=headers()).status_code == 409
    assert client.post(url, json={**valid, "authorized_by": " "}, headers=headers()).status_code == 409
    assert client.post(url, json={"authorized_by": "operator", "confirmation": valid["confirmation"]},
                       headers=headers()).status_code == 422
    response = client.post(url, json=valid, headers=headers())
    assert response.status_code == 409
    assert "campaign is not live authorized" in response.json()["failures"]


def test_disabled_provider_and_missing_sender_never_send(client, monkeypatch):
    import app
    _, campaign = launch(client, "disabled@example.com", datetime.now(timezone.utc) - timedelta(minutes=1))
    assert authorize(client, campaign["id"]).status_code == 200
    monkeypatch.setattr(app, "EMAIL_FROM", "")
    assert execute_canary(client, campaign).status_code == 409
    monkeypatch.setattr(app, "EMAIL_FROM", "approved@example.com")
    monkeypatch.setattr(app, "EMAIL_PROVIDER_NAME", "disabled")
    assert execute_canary(client, campaign).status_code == 503


def test_mock_canary_sends_exactly_one_and_retry_is_idempotent(client, monkeypatch):
    import app
    provider_class = configure_mock(app, monkeypatch)
    _, campaign = launch(client, "one@example.com", datetime.now(timezone.utc) - timedelta(minutes=1))
    original_key = campaign["touches"][0]["idempotency_key"]
    assert authorize(client, campaign["id"]).status_code == 200
    first = execute_canary(client, campaign)
    second = execute_canary(client, campaign)
    assert first.status_code == second.status_code == 200
    assert first.json()["already_executed"] is False
    assert second.json()["already_executed"] is True
    assert len(provider_class.calls) == 1
    assert provider_class.calls[0]["message"] == campaign["touches"][0]["message"]
    assert provider_class.calls[0]["idempotency_key"] == original_key
    execution = client.get(f"/deliveries/{campaign['touches'][0]['id']}/execution", headers=headers()).json()
    assert execution["execution_status"] == "sent" and execution["attempt_count"] == 1
    assert execution["sent_at"] and execution["provider_message_id"].startswith("mock-")
    assert execution["idempotency_key"] == original_key
    # No implicit Day 3/6 activity.
    later = [client.get(f"/deliveries/{item['id']}/execution", headers=headers()).json()
             for item in campaign["touches"][1:]]
    assert all(item["execution_status"] == "pending" and item["attempt_count"] == 0 for item in later)


def test_canary_preflight_and_execution_inspection_are_read_only(client, monkeypatch):
    import app
    configure_mock(app, monkeypatch)
    _, campaign = launch(client, "preflight@example.com")
    delivery_id = campaign["touches"][0]["id"]
    with app.engine.connect() as connection:
        before = connection.exec_driver_sql("SELECT * FROM campaign_touches WHERE id = ?", (delivery_id,)).mappings().one()
    assert client.get(f"/deliveries/{delivery_id}/canary-preflight", headers=headers()).status_code == 200
    assert client.get(f"/deliveries/{delivery_id}/execution", headers=headers()).status_code == 200
    with app.engine.connect() as connection:
        after = connection.exec_driver_sql("SELECT * FROM campaign_touches WHERE id = ?", (delivery_id,)).mappings().one()
    assert before == after


@pytest.mark.parametrize("field, expected", [
    ("skipped", "delivery is skipped"), ("cancelled", "delivery is cancelled"),
])
def test_canary_rejects_skipped_or_cancelled_delivery(client, monkeypatch, field, expected):
    import app
    provider_class = configure_mock(app, monkeypatch)
    _, campaign = launch(client, f"{field}@example.com", datetime.now(timezone.utc) - timedelta(minutes=1))
    assert authorize(client, campaign["id"]).status_code == 200
    with app.SessionLocal() as db:
        touch = db.get(app.Touch, campaign["touches"][0]["id"])
        setattr(touch, field, True)
        db.commit()
    response = execute_canary(client, campaign)
    assert response.status_code == 409 and expected in response.json()["failures"]
    assert provider_class.calls == []


def test_provider_failure_is_persisted_without_retry_or_sent_at(client, monkeypatch):
    import app
    configure_mock(app, monkeypatch)
    _, campaign = launch(client, "failure@example.com", datetime.now(timezone.utc) - timedelta(minutes=1))
    assert authorize(client, campaign["id"]).status_code == 200

    class FailingProvider:
        name = "mock"
        calls = 0
        def send(self, **kwargs):
            self.calls += 1
            raise RuntimeError("secret provider detail")

    provider = FailingProvider()
    monkeypatch.setattr(app, "configured_provider", lambda *args: provider)
    response = execute_canary(client, campaign)
    assert response.status_code == 502 and provider.calls == 1
    state = client.get(f"/deliveries/{campaign['touches'][0]['id']}/execution", headers=headers()).json()
    assert state["execution_status"] == "failed" and state["attempt_count"] == 1
    assert state["sent_at"] is None and "secret provider detail" not in state["last_execution_error"]
    # The service has no automatic provider retry.
    assert provider.calls == 1


def test_concurrent_canary_requests_make_at_most_one_provider_call(client, monkeypatch):
    import app
    configure_mock(app, monkeypatch)
    _, campaign = launch(client, "concurrent@example.com", datetime.now(timezone.utc) - timedelta(minutes=1))
    assert authorize(client, campaign["id"]).status_code == 200

    class HoldingProvider:
        name = "mock"
        calls = 0
        entered = threading.Event()
        release = threading.Event()
        def send(self, **kwargs):
            self.calls += 1
            self.entered.set()
            assert self.release.wait(5)
            from email_providers import EmailSendResult
            return EmailSendResult("concurrent-one")

    provider = HoldingProvider()
    monkeypatch.setattr(app, "configured_provider", lambda *args: provider)
    first_result = []
    thread = threading.Thread(target=lambda: first_result.append(execute_canary(client, campaign)))
    thread.start()
    assert provider.entered.wait(5)
    second = execute_canary(client, campaign)
    provider.release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert provider.calls == 1
    assert first_result[0].status_code == 200
    assert second.status_code == 409


def test_campaign_defaults_safe_and_authorization_validates_confirmation_and_actor(client):
    _, campaign = launch(client)
    assert campaign["dry_run"] is True
    assert campaign["live_authorized"] is False
    assert authorize(client, campaign["id"], confirmation="authorize live outreach").status_code == 409
    assert authorize(client, campaign["id"], authorized_by="   ").status_code == 409
    safety = client.get(f"/campaigns/{campaign['id']}/safety", headers=headers()).json()
    assert safety["dry_run"] is True and safety["live_authorized"] is False


def test_suppressed_prospect_cannot_be_authorized(client):
    prospect_row, campaign = launch(client, "suppressed-live@example.com")
    client.post("/suppressions", json={"email": prospect_row["verified_email"], "reason": "opt-out"}, headers=headers())
    response = authorize(client, campaign["id"])
    assert response.status_code == 409
    assert "prospect is suppressed" in response.json()["failures"]


def test_authorization_is_idempotent_preserves_deliveries_keys_and_never_sends(client, monkeypatch):
    import app

    _, campaign = launch(client, "authorized@example.com", datetime.now(timezone.utc) + timedelta(days=1))
    before = client.get(f"/campaigns/{campaign['id']}/deliveries", headers=headers()).json()
    monkeypatch.setattr(app, "deliver", lambda *args: pytest.fail("external delivery must not be called"))
    first = authorize(client, campaign["id"])
    second = authorize(client, campaign["id"])
    assert first.status_code == second.status_code == 200
    assert first.json()["dry_run"] is False and first.json()["live_authorized"] is True
    assert first.json()["live_authorized_at"] == second.json()["live_authorized_at"]
    after = client.get(f"/campaigns/{campaign['id']}/deliveries", headers=headers()).json()
    assert len(before) == len(after) == 3
    assert [item["idempotency_key"] for item in before] == [item["idempotency_key"] for item in after]
    assert all(not item["dry_run"] for item in after)


def test_later_suppression_blocks_eligibility_and_preserves_sent_history(client):
    import app

    p, campaign = launch(client, "later-suppressed@example.com", datetime.now(timezone.utc) - timedelta(days=1))
    # Preserve a historical sent record; suppression must only mutate future unsent rows.
    with app.SessionLocal() as db:
        row = db.get(app.Campaign, campaign["id"])
        row.touches[0].status = "sent"
        row.touches[0].sent_at = datetime.now(timezone.utc)
        db.commit()
    authorize(client, campaign["id"])
    client.post("/suppressions", json={"email": p["verified_email"], "reason": "customer opt-out"}, headers=headers())
    deliveries = client.get(f"/campaigns/{campaign['id']}/deliveries", headers=headers()).json()
    assert deliveries[0]["status"] == "sent" and deliveries[0]["sent_at"] is not None
    assert all(item["skipped"] for item in deliveries[1:])
    assert all("suppression" in item["cancellation_or_skip_reason"] for item in deliveries[1:])
    safety = client.get(f"/campaigns/{campaign['id']}/safety", headers=headers()).json()
    assert safety["suppressed"] is True and safety["eligible_delivery_count"] == 0


def test_safety_inspection_is_read_only(client):
    import app

    _, campaign = launch(client, "readonly-safety@example.com")
    with app.engine.connect() as connection:
        before = connection.exec_driver_sql("SELECT * FROM campaign_touches ORDER BY id").mappings().all()
    first = client.get(f"/campaigns/{campaign['id']}/safety", headers=headers())
    second = client.get(f"/campaigns/{campaign['id']}/safety", headers=headers())
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    with app.engine.connect() as connection:
        after = connection.exec_driver_sql("SELECT * FROM campaign_touches ORDER BY id").mappings().all()
    assert before == after
