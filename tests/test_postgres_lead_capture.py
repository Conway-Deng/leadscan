from datetime import datetime, timedelta, timezone

import pytest

from lead_capture import (
    DEFAULT_SOURCE,
    LeadStoreError,
    POSTGRES_CONNECT_TIMEOUT_SECONDS,
    PostgresLeadStore,
)


FAKE_DATABASE_URL = "postgresql://user:fake-password@example.invalid/leadscan"


class FakeCursor:
    def __init__(self, row=(1,)):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, *, insert_row=(1,)):
        self.calls = []
        self.commits = 0
        self.closed = False
        self.insert_row = insert_row

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return FakeCursor(self.insert_row)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class FakeConnector:
    def __init__(self, *connections):
        self.connections = list(connections)
        self.calls = []

    def __call__(self, database_url, **kwargs):
        self.calls.append((database_url, kwargs))
        return self.connections.pop(0)


def test_postgres_schema_and_insert_use_constant_sql_and_bound_parameters():
    schema_conn = FakeConnection()
    insert_conn = FakeConnection(insert_row=(42,))
    connector = FakeConnector(schema_conn, insert_conn)
    fixed_time = datetime(
        2026,
        8,
        31,
        19,
        30,
        tzinfo=timezone(timedelta(hours=8)),
    )
    store = PostgresLeadStore(
        FAKE_DATABASE_URL,
        now_fn=lambda: fixed_time,
        connect_fn=connector,
    )
    name = "O'Connor"
    email = "owner+o'connor@example.com"
    website = "https://example.com/path?name=o'connor"

    row_id = store.save_lead(
        contact_name=f"  {name}  ",
        email=f"  {email}  ",
        website_url=f"  {website}  ",
    )

    assert row_id == 42
    schema_sql, schema_params = schema_conn.calls[0]
    assert "CREATE TABLE IF NOT EXISTS public_leads" in schema_sql
    assert schema_params is None
    assert name not in schema_sql
    assert email not in schema_sql
    assert website not in schema_sql

    insert_sql, insert_params = insert_conn.calls[0]
    assert "VALUES (%s, %s, %s, %s, %s)" in insert_sql
    assert "RETURNING id" in insert_sql
    assert name not in insert_sql
    assert email not in insert_sql
    assert website not in insert_sql
    assert insert_params == (
        "2026-08-31T11:30:00Z",
        name,
        email,
        website,
        DEFAULT_SOURCE,
    )
    assert schema_conn.commits == 1
    assert insert_conn.commits == 1
    assert schema_conn.closed is True
    assert insert_conn.closed is True
    assert connector.calls == [
        (FAKE_DATABASE_URL, {"connect_timeout": POSTGRES_CONNECT_TIMEOUT_SECONDS}),
        (FAKE_DATABASE_URL, {"connect_timeout": POSTGRES_CONNECT_TIMEOUT_SECONDS}),
    ]


@pytest.mark.parametrize(
    "name,email,url",
    [
        (123, "valid@example.com", "https://example.com"),
        ("a" * 121, "valid@example.com", "https://example.com"),
        ("bad\x00name", "valid@example.com", "https://example.com"),
        ("Valid Name", 123, "https://example.com"),
        ("Valid Name", "", "https://example.com"),
        ("Valid Name", "   ", "https://example.com"),
        ("Valid Name", "a" * 255, "https://example.com"),
        ("Valid Name", "bad\x00email@example.com", "https://example.com"),
        ("Valid Name", "valid@example.com", 123),
        ("Valid Name", "valid@example.com", ""),
        ("Valid Name", "valid@example.com", "   "),
        ("Valid Name", "valid@example.com", "https://example.com/" + "a" * 2040),
        ("Valid Name", "valid@example.com", "https://example.com/\x00bad"),
    ],
)
def test_postgres_validation_matches_sqlite_without_connecting(name, email, url):
    def unexpected_connect(*args, **kwargs):
        raise AssertionError("invalid input must be rejected before connecting")

    store = PostgresLeadStore(FAKE_DATABASE_URL, connect_fn=unexpected_connect)
    with pytest.raises(ValueError):
        store.save_lead(contact_name=name, email=email, website_url=url)


def test_postgres_schema_failure_is_generic_and_preserves_internal_cause():
    internal = RuntimeError(f"could not connect to {FAKE_DATABASE_URL}")

    def fail_connect(*args, **kwargs):
        raise internal

    store = PostgresLeadStore(FAKE_DATABASE_URL, connect_fn=fail_connect)
    with pytest.raises(LeadStoreError) as caught:
        store.save_lead(
            contact_name="Secret Owner",
            email="secret@example.com",
            website_url="https://secret.example.com",
        )

    assert str(caught.value) == "Failed to store lead"
    assert FAKE_DATABASE_URL not in str(caught.value)
    assert "fake-password" not in str(caught.value)
    assert caught.value.__cause__ is internal


def test_postgres_insert_failure_is_generic():
    schema_conn = FakeConnection()

    def connector(database_url, **kwargs):
        if not schema_conn.calls:
            return schema_conn
        raise RuntimeError(f"password from {database_url} rejected")

    store = PostgresLeadStore(FAKE_DATABASE_URL, connect_fn=connector)
    with pytest.raises(LeadStoreError) as caught:
        store.save_lead(
            contact_name="Alice",
            email="alice@example.com",
            website_url="https://example.com",
        )

    assert str(caught.value) == "Failed to store lead"
    assert "fake-password" not in str(caught.value)


def test_postgres_driver_is_lazy_for_sqlite_only_import(monkeypatch):
    imported = []

    def fake_import(name, *args, **kwargs):
        imported.append(name)
        raise AssertionError("Psycopg must not be imported by store construction")

    monkeypatch.setattr("builtins.__import__", fake_import)
    PostgresLeadStore(FAKE_DATABASE_URL, connect_fn=lambda *args, **kwargs: None)
    assert "psycopg" not in imported
