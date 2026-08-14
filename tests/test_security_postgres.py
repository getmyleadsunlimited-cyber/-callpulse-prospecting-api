"""PostgreSQL-only concurrency and legacy migration security regressions."""
import os
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

POSTGRES_URL = os.getenv("CALLPULSE_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="CALLPULSE_TEST_POSTGRES_URL is not configured")
ROOT = Path(__file__).parents[1]


@pytest.fixture()
def pg_schema():
    engine = create_engine(POSTGRES_URL)
    schema = "callpulse_test_" + uuid.uuid4().hex
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped = create_engine(POSTGRES_URL, connect_args={"options": f"-csearch_path={schema}"})
    try:
        yield scoped
    finally:
        scoped.dispose()
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


def test_atomic_login_limit_reserves_before_password_verification(pg_schema, monkeypatch):
    import app
    from fastapi.testclient import TestClient
    app.Base.metadata.create_all(pg_schema)
    barrier = threading.Barrier(app.LOGIN_MAX_FAILURES + 6)
    verified = []
    statuses = []
    lock = threading.Lock()
    monkeypatch.setattr(app, "SessionLocal", sessionmaker(pg_schema, expire_on_commit=False))

    def password_verification(password, encoded):
        with lock:
            verified.append(1)
        return False

    monkeypatch.setattr(app, "verify_password", password_verification)
    client = TestClient(app.app)

    def attempt():
        barrier.wait()
        response = client.post("/auth/login", json={
            "email": "concurrent@example.com", "password": "wrong", "account_id": "concurrent-account",
        })
        with lock:
            statuses.append(response.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(app.LOGIN_MAX_FAILURES + 6)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert len(verified) == app.LOGIN_MAX_FAILURES
    assert statuses.count(401) == app.LOGIN_MAX_FAILURES
    assert statuses.count(429) == 6


def test_recipient_change_serializes_with_provider_send(pg_schema, monkeypatch):
    import app
    from fastapi.testclient import TestClient

    app.Base.metadata.create_all(pg_schema)
    sessions = sessionmaker(pg_schema, expire_on_commit=False)
    monkeypatch.setattr(app, "SessionLocal", sessions)
    monkeypatch.setattr(app, "API_KEY", "send-secret")
    monkeypatch.setattr(app, "INTERNAL_ADMIN_API_KEY", "internal-secret")
    monkeypatch.setattr(app, "EMAIL_PROVIDER_NAME", "mock")
    monkeypatch.setattr(app, "EMAIL_FROM", "approved@example.com")

    now = app.utcnow()
    with sessions() as db:
        prospect = app.Prospect(
            company_name="Concurrent recipient", website="https://example.com",
            industry="Roofing", score=90, why_now="Ready",
            ai_recovery_opportunity="Recovery", workspace_id=app.DEFAULT_WORKSPACE_ID,
            verified_email="authorized@example.com", email_verified=True,
            status="campaign_active",
        )
        campaign = app.Campaign(
            prospect=prospect, starts_at=now, ends_at=now + app.timedelta(days=7),
            dry_run=False, live_authorized=True, live_authorized_at=now,
            live_authorized_by="operator", authorized_recipient_email="authorized@example.com",
        )
        touch = app.Touch(
            campaign=campaign, day=0, scheduled_at=now - app.timedelta(seconds=1),
            message="Persisted body", subject="Persisted subject",
            idempotency_key="concurrent-recipient-send", dry_run=False,
        )
        db.add(prospect)
        db.commit()
        campaign_id, prospect_id, touch_id = campaign.id, prospect.id, touch.id

    provider_entered = threading.Event()
    release_provider = threading.Event()
    mutation_done = threading.Event()
    recipients = []

    class BlockingProvider:
        name = "mock"

        def send(self, *, recipient, **kwargs):
            recipients.append(recipient)
            provider_entered.set()
            assert release_provider.wait(5)
            return SimpleNamespace(message_id="provider-message", correlation_id="provider-correlation")

    monkeypatch.setattr(app, "configured_provider", lambda *args, **kwargs: BlockingProvider())
    results = {}

    def execute():
        with TestClient(app.app) as client:
            results["send"] = client.post(
                f"/campaigns/{campaign_id}/canary-execute",
                headers={"Authorization": "Bearer send-secret"},
                json={"delivery_id": touch_id, "authorized_by": "operator",
                      "confirmation": "EXECUTE ONE CANARY DELIVERY"},
            )

    def mutate():
        with TestClient(app.app) as client:
            results["mutation"] = client.post(
                f"/internal/prospects/{prospect_id}/verify-email",
                headers={"Authorization": "Bearer internal-secret"},
                json={"verified_email": "replacement@example.com"},
            )
        mutation_done.set()

    send_thread = threading.Thread(target=execute)
    send_thread.start()
    assert provider_entered.wait(5)
    mutation_thread = threading.Thread(target=mutate)
    mutation_thread.start()
    time.sleep(0.25)
    assert not mutation_done.is_set(), "recipient mutation bypassed the in-flight send lock"
    release_provider.set()
    send_thread.join(5)
    mutation_thread.join(5)

    assert results["send"].status_code == 200
    assert results["mutation"].status_code == 200
    assert recipients == ["authorized@example.com"]
    with sessions() as db:
        stored_touch = db.get(app.Touch, touch_id)
        stored_campaign = db.get(app.Campaign, campaign_id)
        stored_prospect = db.get(app.Prospect, prospect_id)
        assert stored_touch.execution_status == "sent"
        assert stored_prospect.verified_email == "replacement@example.com"
        assert stored_campaign.live_authorized is False


def prepare_legacy(engine, users, grants=(), registry=()):
    with engine.begin() as connection:
        connection.exec_driver_sql((ROOT / "migrations/008_customer_users_rbac.sql").read_text())
        for user in users:
            connection.execute(text("""INSERT INTO users
                (id,email,password_hash,account_id,account_type,primary_workspace_id,role,active)
                VALUES (:id,:email,'hash',:account,:type,:workspace,'owner',TRUE)"""), user)
        for user_id, workspace in grants:
            connection.execute(text("INSERT INTO user_workspace_access(user_id,workspace_id) VALUES (:u,:w)"),
                               {"u": user_id, "w": workspace})
        if registry:
            connection.exec_driver_sql("CREATE TABLE accounts (id VARCHAR(100) PRIMARY KEY, account_type VARCHAR(20) NOT NULL)")
            connection.exec_driver_sql("""CREATE TABLE workspace_ownership_registry (
                workspace_id VARCHAR(100) PRIMARY KEY, owner_account_id VARCHAR(100) NOT NULL REFERENCES accounts(id),
                workspace_type VARCHAR(20) NOT NULL)""")
            for account, account_type in {(row[1], row[2]) for row in registry}:
                connection.execute(text("INSERT INTO accounts VALUES (:a,:t)"), {"a": account, "t": account_type})
            for workspace, account, account_type in registry:
                connection.execute(text("INSERT INTO workspace_ownership_registry VALUES (:w,:a,:t)"),
                                   {"w": workspace, "a": account, "t": account_type})


def apply_009(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql((ROOT / "migrations/009_security_hardening.sql").read_text())


@pytest.mark.parametrize("users", [
    [dict(id=1,email="d1@x.test",account="direct-a",type="direct",workspace="shared"),
     dict(id=2,email="d2@x.test",account="direct-b",type="direct",workspace="shared")],
    [dict(id=1,email="d@x.test",account="direct",type="direct",workspace="shared"),
     dict(id=2,email="c@x.test",account="client",type="client",workspace="shared")],
    [dict(id=2,email="d@x.test",account="direct",type="direct",workspace="shared"),
     dict(id=1,email="c@x.test",account="client",type="client",workspace="shared")],
    [dict(id=1,email="c@x.test",account="client",type="client",workspace="shared"),
     dict(id=2,email="a@x.test",account="agency",type="agency",workspace="shared")],
    [dict(id=2,email="c@x.test",account="client",type="client",workspace="shared"),
     dict(id=1,email="a@x.test",account="agency",type="agency",workspace="shared")],
])
def test_migration_rejects_unresolved_ownership_regardless_of_order(pg_schema, users):
    prepare_legacy(pg_schema, users)
    with pytest.raises(Exception, match="Ambiguous legacy workspace ownership"):
        apply_009(pg_schema)


def test_migration_accepts_explicit_client_owner_and_delegated_agency_grant(pg_schema):
    users = [dict(id=1,email="a@x.test",account="agency",type="agency",workspace="agency-home"),
             dict(id=2,email="c@x.test",account="client",type="client",workspace="client-home")]
    registry = [("agency-home", "agency", "agency"), ("client-home", "client", "client")]
    prepare_legacy(pg_schema, users, grants=[(1, "agency-home"), (1, "client-home"), (2, "client-home")],
                   registry=registry)
    apply_009(pg_schema)
    with pg_schema.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM agency_workspace_access")) == 1
        assert connection.scalar(text("SELECT owner_account_id FROM workspaces WHERE id='client-home'")) == "client"


@pytest.mark.parametrize("owner_first", [True, False])
@pytest.mark.parametrize("owner_type,other_type", [("direct", "client"), ("client", "direct")])
def test_ambiguous_direct_client_registry_owner_migrates_and_quarantines_non_owner(
        pg_schema, owner_first, owner_type, other_type):
    owner = dict(id=1 if owner_first else 2, email="owner@x.test", account="authoritative",
                 type=owner_type, workspace="shared")
    other = dict(id=2 if owner_first else 1, email="other@x.test", account="non-owner",
                 type=other_type, workspace="shared")
    prepare_legacy(pg_schema, [owner, other], grants=[(owner["id"], "shared"), (other["id"], "shared")],
                   registry=[("shared", "authoritative", owner_type)])
    apply_009(pg_schema)
    with pg_schema.connect() as connection:
        assert connection.scalar(text("SELECT owner_account_id FROM workspaces WHERE id='shared'")) == "authoritative"
        assert connection.scalar(text("SELECT count(*) FROM account_memberships")) == 1
        assert connection.scalar(text("SELECT account_id FROM account_memberships")) == "authoritative"
        assert connection.scalar(text("SELECT count(*) FROM membership_workspace_access")) == 1
        assert connection.scalar(text("SELECT count(*) FROM agency_workspace_access")) == 0
        assert connection.scalar(text("""SELECT count(*) FROM legacy_tenancy_quarantine
            WHERE account_id='non-owner' AND record_type='membership'""")) == 1
        assert connection.scalar(text("""SELECT count(*) FROM legacy_tenancy_quarantine
            WHERE account_id='non-owner' AND record_type='workspace_grant'""")) == 1


@pytest.mark.parametrize("agency_first", [True, False])
def test_ambiguous_agency_client_registry_owner_requires_explicit_delegation(pg_schema, agency_first):
    agency = dict(id=1 if agency_first else 2, email="agency@x.test", account="agency",
                  type="agency", workspace="shared-client")
    client = dict(id=2 if agency_first else 1, email="client@x.test", account="client",
                  type="client", workspace="shared-client")
    prepare_legacy(pg_schema, [agency, client],
                   grants=[(agency["id"], "shared-client"), (client["id"], "shared-client")],
                   registry=[("shared-client", "client", "client")])
    apply_009(pg_schema)
    with pg_schema.connect() as connection:
        assert connection.scalar(text(
            "SELECT owner_account_id FROM workspaces WHERE id='shared-client'")) == "client"
        assert connection.scalar(text("SELECT count(*) FROM account_memberships")) == 2
        assert connection.scalar(text("SELECT count(*) FROM agency_workspace_access")) == 1
        assert connection.scalar(text("""SELECT count(*) FROM agency_workspace_access
            WHERE agency_account_id='agency' AND workspace_id='shared-client'""")) == 1
        assert connection.scalar(text("SELECT count(*) FROM membership_workspace_access")) == 2
        assert connection.scalar(text("SELECT count(*) FROM legacy_tenancy_quarantine")) == 0


def test_ambiguous_agency_claim_without_explicit_grant_is_quarantined(pg_schema):
    users = [dict(id=1,email="agency@x.test",account="agency",type="agency",workspace="shared-client"),
             dict(id=2,email="client@x.test",account="client",type="client",workspace="shared-client")]
    prepare_legacy(pg_schema, users, grants=[(2, "shared-client")],
                   registry=[("shared-client", "client", "client")])
    apply_009(pg_schema)
    with pg_schema.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM agency_workspace_access")) == 0
        assert connection.scalar(text("SELECT count(*) FROM account_memberships")) == 1
        assert connection.scalar(text("SELECT account_id FROM account_memberships")) == "client"
        assert connection.scalar(text("""SELECT count(*) FROM legacy_tenancy_quarantine
            WHERE account_id='agency' AND record_type='membership'""")) == 1


def test_migration_quarantines_non_primary_unregistered_grants(pg_schema):
    users = [dict(id=1,email="d@x.test",account="direct",type="direct",workspace="direct-home")]
    prepare_legacy(pg_schema, users, grants=[(1, "not-authoritative")])
    apply_009(pg_schema)
    with pg_schema.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM membership_workspace_access")) == 0
        assert connection.scalar(text("""SELECT count(*) FROM legacy_tenancy_quarantine
            WHERE workspace_id='not-authoritative' AND record_type='workspace_grant'""")) == 1
