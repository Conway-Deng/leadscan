"""Private SQLite persistence primitive for inbound public-audit leads."""

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import threading

DEFAULT_SOURCE = "public_audit_widget"

MAX_CONTACT_NAME_LENGTH = 120
MAX_EMAIL_LENGTH = 254
MAX_WEBSITE_URL_LENGTH = 2048


class LeadStoreError(RuntimeError):
    """Generic error raised when storing a lead fails without exposing PII or path."""
    pass


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

        now = self.now_fn()
        if now.tzinfo is None:
            now_utc = now.replace(tzinfo=timezone.utc)
        else:
            now_utc = now.astimezone(timezone.utc)
        created_at = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

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
