import importlib
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

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


def test_auth_fails_closed(client):
    assert client.get("/prospects").status_code == 401


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


def test_suppression_blocks_launch(client):
    p = client.post("/prospects", json=prospect("blocked@example.com"), headers=headers()).json()
    assert client.post("/suppressions", json={"email": "BLOCKED@example.com", "reason": "opt-out"}, headers=headers()).status_code == 201
    response = client.post(f"/prospects/{p['id']}/campaigns", json={"idempotency_key": "request-456"}, headers=headers())
    assert response.status_code == 409
