"""Apply PostgreSQL migrations and refuse startup when the schema drifts."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
MIGRATION_LOCK_ID = 1_347_008_357


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def apply_postgresql_migrations(engine: Engine) -> None:
    """Apply each migration once under a transaction-scoped deployment lock."""
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": MIGRATION_LOCK_ID})
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = set(connection.execute(text("SELECT name FROM schema_migrations")).scalars())
        for migration in migrations:
            if migration.name in applied:
                continue
            connection.exec_driver_sql(migration.read_text(encoding="utf-8"))
            connection.execute(
                text("INSERT INTO schema_migrations (name) VALUES (:name)"),
                {"name": migration.name},
            )
            print(f"Applied migration {migration.name}")


def schema_drift(engine: Engine, metadata: MetaData) -> list[str]:
    """Return every model table or column absent from the connected database."""
    database = inspect(engine)
    actual_tables = set(database.get_table_names())
    drift: list[str] = []
    for table in metadata.sorted_tables:
        if table.name not in actual_tables:
            drift.append(f"missing table: {table.name}")
            continue
        actual_columns = {column["name"] for column in database.get_columns(table.name)}
        drift.extend(
            f"missing column: {table.name}.{column.name}"
            for column in table.columns
            if column.name not in actual_columns
        )
    return drift


def main() -> None:
    database_url = normalize_database_url(os.environ.get("DATABASE_URL", "sqlite:////tmp/callpulse.db"))
    engine = create_engine(database_url, pool_pre_ping=True)
    if engine.dialect.name == "postgresql":
        apply_postgresql_migrations(engine)

    # Import after migrations: app creates an empty development database, but
    # create_all cannot conceal missing columns in an existing PostgreSQL table.
    from app import Base

    drift = schema_drift(engine, Base.metadata)
    if drift:
        raise RuntimeError("Database schema does not match SQLAlchemy models:\n- " + "\n- ".join(drift))
    print("Database schema matches SQLAlchemy models")


if __name__ == "__main__":
    main()
