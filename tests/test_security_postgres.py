"""PostgreSQL-only concurrency and legacy migration security regressions."""
import os
import threading
import uuid
from pathlib import Path

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


def test_migration_rejects_non_primary_unregistered_or_cross_account_grants(pg_schema):
    users = [dict(id=1,email="d@x.test",account="direct",type="direct",workspace="direct-home")]
    prepare_legacy(pg_schema, users, grants=[(1, "not-authoritative")])
    with pytest.raises(Exception, match="Invalid legacy workspace grant"):
        apply_009(pg_schema)
