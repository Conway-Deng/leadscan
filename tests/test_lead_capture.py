"""Unit tests for SQLiteLeadStore private lead persistence primitive."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import sqlite3

import pytest

from lead_capture import (
    DEFAULT_SOURCE,
    LeadStoreError,
    SQLiteLeadStore,
)


def test_constructor_does_not_create_database(tmp_path):
    db_path = tmp_path / "leads.sqlite3"
    store = SQLiteLeadStore(db_path)
    assert not db_path.exists()
    assert store.path == db_path


def test_first_save_creates_database_and_schema(tmp_path):
    db_path = tmp_path / "subdir" / "leads.sqlite3"
    store = SQLiteLeadStore(db_path)
    assert not db_path.exists()

    row_id = store.save_lead(
        contact_name="Alice Smith",
        email="alice@example.com",
        website_url="https://alice.example.com",
    )
    assert isinstance(row_id, int)
    assert row_id > 0
    assert db_path.exists()

    if os.name == "posix":
        mode = os.stat(db_path).st_mode & 0o777
        assert mode == 0o600

    conn = sqlite3.connect(str(db_path))
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(public_leads)").fetchall()]
        assert columns == [
            "id",
            "created_at",
            "contact_name",
            "email",
            "website_url",
            "source",
        ]
    finally:
        conn.close()


def test_save_lead_persists_trimmed_fields_and_fixed_source(tmp_path):
    db_path = tmp_path / "leads.sqlite3"
    store = SQLiteLeadStore(db_path)

    row_id = store.save_lead(
        contact_name="  Bob Builder  ",
        email="  bob@example.com  ",
        website_url="  https://example.com/site  ",
    )
    assert row_id == 1

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT id, created_at, contact_name, email, website_url, source FROM public_leads WHERE id = ?",
            (row_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == 1
        assert isinstance(row[1], str)
        assert row[2] == "Bob Builder"
        assert row[3] == "bob@example.com"
        assert row[4] == "https://example.com/site"
        assert row[5] == DEFAULT_SOURCE
        assert row[5] == "public_audit_widget"
    finally:
        conn.close()


def test_save_lead_serializes_injected_time_as_utc_z(tmp_path):
    db_path = tmp_path / "leads.sqlite3"
    myt = timezone(timedelta(hours=8))
    fixed_time = datetime(2026, 8, 31, 19, 30, 0, tzinfo=myt)

    store = SQLiteLeadStore(db_path, now_fn=lambda: fixed_time)
    row_id = store.save_lead(
        contact_name="Time Tester",
        email="time@example.com",
        website_url="https://time.example.com",
    )

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT created_at FROM public_leads WHERE id = ?", (row_id,)).fetchone()
        assert row is not None
        assert row[0] == "2026-08-31T11:30:00Z"
    finally:
        conn.close()


def test_save_lead_handles_quotes_as_data_not_sql(tmp_path):
    db_path = tmp_path / "leads.sqlite3"
    store = SQLiteLeadStore(db_path)

    name = "O'Connor"
    email = "owner+o'connor@example.com"
    url = "https://example.com/path?name=o'connor"

    row_id = store.save_lead(contact_name=name, email=email, website_url=url)
    assert row_id == 1

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT contact_name, email, website_url FROM public_leads").fetchall()
        assert len(rows) == 1
        assert rows[0] == (name, email, url)
    finally:
        conn.close()


def test_multiple_leads_receive_distinct_ids(tmp_path):
    db_path = tmp_path / "leads.sqlite3"
    store = SQLiteLeadStore(db_path)

    id1 = store.save_lead(contact_name="Lead One", email="one@example.com", website_url="https://one.example.com")
    id2 = store.save_lead(contact_name="Lead Two", email="two@example.com", website_url="https://two.example.com")
    id3 = store.save_lead(contact_name="Lead Three", email="three@example.com", website_url="https://three.example.com")

    assert id1 > 0 and id2 > 0 and id3 > 0
    assert len({id1, id2, id3}) == 3

    conn = sqlite3.connect(str(db_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM public_leads").fetchone()[0]
        assert count == 3
    finally:
        conn.close()


def test_concurrent_writes_do_not_lose_leads(tmp_path):
    db_path = tmp_path / "leads.sqlite3"
    store = SQLiteLeadStore(db_path)

    count = 12

    def save_one(index):
        return store.save_lead(
            contact_name=f"User {index}",
            email=f"user_{index}@example.com",
            website_url=f"https://example_{index}.com",
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(save_one, i) for i in range(count)]
        ids = [f.result() for f in futures]

    assert len(ids) == count
    assert len(set(ids)) == count

    conn = sqlite3.connect(str(db_path))
    try:
        stored_count = conn.execute("SELECT COUNT(*) FROM public_leads").fetchone()[0]
        assert stored_count == count
    finally:
        conn.close()


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
def test_invalid_lead_fields_are_rejected_without_creating_database(tmp_path, name, email, url):
    db_path = tmp_path / "leads.sqlite3"
    store = SQLiteLeadStore(db_path)

    with pytest.raises(ValueError) as exc_info:
        store.save_lead(contact_name=name, email=email, website_url=url)

    err_msg = str(exc_info.value)
    if isinstance(name, str) and name:
        assert name not in err_msg
    if isinstance(email, str) and email:
        assert email not in err_msg
    if isinstance(url, str) and url:
        assert url not in err_msg

    assert not db_path.exists()


def test_schema_contains_no_client_tracking_fields(tmp_path):
    db_path = tmp_path / "leads.sqlite3"
    store = SQLiteLeadStore(db_path)
    store.save_lead(contact_name="Alice", email="alice@example.com", website_url="https://example.com")

    conn = sqlite3.connect(str(db_path))
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(public_leads)").fetchall()]
        assert columns == [
            "id",
            "created_at",
            "contact_name",
            "email",
            "website_url",
            "source",
        ]
        col_text = " ".join(columns).lower()
        for forbidden in ["ip", "client_ip", "user_agent", "headers", "cookie", "fingerprint"]:
            assert forbidden not in col_text
    finally:
        conn.close()


def test_storage_failure_raises_generic_error_without_lead_values(tmp_path):
    blocked_file = tmp_path / "blocked"
    blocked_file.write_text("regular file", encoding="utf-8")
    db_path = blocked_file / "leads.sqlite3"

    store = SQLiteLeadStore(db_path)
    secret_name = "Secret Agent"
    secret_email = "secret_agent@example.com"
    secret_url = "https://classified.example.com"

    with pytest.raises(LeadStoreError) as exc_info:
        store.save_lead(contact_name=secret_name, email=secret_email, website_url=secret_url)

    assert str(exc_info.value) == "Failed to store lead"
    assert secret_name not in str(exc_info.value)
    assert secret_email not in str(exc_info.value)
    assert secret_url not in str(exc_info.value)


def test_sqlite_lead_files_are_gitignored():
    gitignore_text = Path(".gitignore").read_text(encoding="utf-8")
    assert "*.sqlite3" in gitignore_text
    assert "*.sqlite3-journal" in gitignore_text
    assert "*.sqlite3-wal" in gitignore_text
    assert "*.sqlite3-shm" in gitignore_text
    assert "*.jsonl" in gitignore_text
