"""Private SQLite and Postgres persistence primitives for public-audit leads."""

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import threading

DEFAULT_SOURCE = "public_audit_widget"

MAX_CONTACT_NAME_LENGTH = 120
MAX_EMAIL_LENGTH = 254
MAX_WEBSITE_URL_LENGTH = 2048
POSTGRES_CONNECT_TIMEOUT_SECONDS = 5


class LeadStoreError(RuntimeError):
    """Generic error raised when storing a lead fails without exposing PII or path."""
    pass


def _normalize_inputs(contact_name, email, website_url):
    if not isinstance(contact_name, str):
        raise ValueError("Invalid contact_name")
    contact_name = contact_name.strip()
    if "\x00" in contact_name or len(contact_name) > MAX_CONTACT_NAME_LENGTH:
        raise ValueError("Invalid contact_name")

    if not isinstance(email, str):
        raise ValueError("Invalid email")
    email = email.strip()
    if not email or "\x00" in email or len(email) > MAX_EMAIL_LENGTH:
        raise ValueError("Invalid email")

    if not isinstance(website_url, str):
        raise ValueError("Invalid website_url")
    website_url = website_url.strip()
    if not website_url or "\x00" in website_url or len(website_url) > MAX_WEBSITE_URL_LENGTH:
        raise ValueError("Invalid website_url")

    return contact_name, email, website_url


def _utc_timestamp(now):
    if now.tzinfo is None:
        now_utc = now.replace(tzinfo=timezone.utc)
    else:
        now_utc = now.astimezone(timezone.utc)
    return now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect_postgres(database_url, **kwargs):
    # Keep Psycopg optional for SQLite-only/local use and import it only when a
    # configured Postgres store actually opens a connection.
    import psycopg

    return psycopg.connect(database_url, **kwargs)


class SQLiteLeadStore:
    """Thread-safe SQLite store for inbound public leads."""

    def __init__(self, path, now_fn=None):
        if not path or (isinstance(path, str) and not path.strip()):
            raise ValueError("Invalid database path")
        self.path = Path(path)
        self.now_fn = now_fn if now_fn is not None else (lambda: datetime.now(timezone.utc))
        self._init_lock = threading.Lock()
        self._initialized = False

    def _normalize_inputs(self, contact_name, email, website_url):
        return _normalize_inputs(contact_name, email, website_url)

    def _ensure_initialized(self):
        with self._init_lock:
            if self._initialized:
                return

            try:
                parent = self.path.parent
                if parent and not parent.exists():
                    parent.mkdir(parents=True, exist_ok=True)

                if not self.path.exists():
                    try:
                        fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                        os.close(fd)
                    except FileExistsError:
                        pass

                if os.name == "posix":
                    os.chmod(str(self.path), 0o600)

                conn = sqlite3.connect(str(self.path), timeout=5.0)
                try:
                    conn.execute("PRAGMA busy_timeout = 5000")
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS public_leads (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            created_at TEXT NOT NULL,
                            contact_name TEXT NOT NULL,
                            email TEXT NOT NULL,
                            website_url TEXT NOT NULL,
                            source TEXT NOT NULL
                        )
                        """
                    )
                    conn.commit()
                finally:
                    conn.close()

                self._initialized = True
            except Exception as exc:
                raise LeadStoreError("Failed to store lead") from exc

    def save_lead(self, *, contact_name, email, website_url):
        contact_name, email, website_url = self._normalize_inputs(contact_name, email, website_url)

        created_at = _utc_timestamp(self.now_fn())

        self._ensure_initialized()

        try:
            conn = sqlite3.connect(str(self.path), timeout=5.0)
            try:
                conn.execute("PRAGMA busy_timeout = 5000")
                cursor = conn.execute(
                    """
                    INSERT INTO public_leads
                        (created_at, contact_name, email, website_url, source)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (created_at, contact_name, email, website_url, DEFAULT_SOURCE),
                )
                row_id = cursor.lastrowid
                conn.commit()
                return int(row_id)
            finally:
                conn.close()
        except LeadStoreError:
            raise
        except Exception as exc:
            raise LeadStoreError("Failed to store lead") from exc


class PostgresLeadStore:
    """Lazy, thread-safe Postgres store for hosted public-audit leads."""

    _SCHEMA_SQL = """
        CREATE TABLE IF NOT EXISTS public_leads (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL,
            contact_name VARCHAR(120) NOT NULL,
            email VARCHAR(254) NOT NULL,
            website_url VARCHAR(2048) NOT NULL,
            source TEXT NOT NULL
        )
    """
    _INSERT_SQL = """
        INSERT INTO public_leads
            (created_at, contact_name, email, website_url, source)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
    """

    def __init__(self, database_url, now_fn=None, connect_fn=None):
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("Invalid database URL")
        self._database_url = database_url.strip()
        self.now_fn = now_fn if now_fn is not None else (lambda: datetime.now(timezone.utc))
        self._connect_fn = connect_fn if connect_fn is not None else _connect_postgres
        self._init_lock = threading.Lock()
        self._initialized = False

    def _connect(self):
        return self._connect_fn(
            self._database_url,
            connect_timeout=POSTGRES_CONNECT_TIMEOUT_SECONDS,
        )

    def _ensure_initialized(self):
        with self._init_lock:
            if self._initialized:
                return
            try:
                conn = self._connect()
                try:
                    conn.execute(self._SCHEMA_SQL)
                    conn.commit()
                finally:
                    conn.close()
                self._initialized = True
            except Exception as exc:
                raise LeadStoreError("Failed to store lead") from exc

    def save_lead(self, *, contact_name, email, website_url):
        contact_name, email, website_url = _normalize_inputs(
            contact_name,
            email,
            website_url,
        )
        created_at = _utc_timestamp(self.now_fn())
        self._ensure_initialized()

        try:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    self._INSERT_SQL,
                    (
                        created_at,
                        contact_name,
                        email,
                        website_url,
                        DEFAULT_SOURCE,
                    ),
                )
                row = cursor.fetchone()
                if not row:
                    raise RuntimeError("Lead insert returned no id")
                conn.commit()
                return int(row[0])
            finally:
                conn.close()
        except LeadStoreError:
            raise
        except Exception as exc:
            raise LeadStoreError("Failed to store lead") from exc
