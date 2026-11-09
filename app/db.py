"""
SQLite-backed conversion log. Survives service restarts; browsable from the
web UI's Log screen.
"""
import sqlite3
import threading
from pathlib import Path

from . import config as config_module

DB_PATH = config_module.CONFIG_DIR / "log.db"
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    profile_id TEXT,
    invoice_number TEXT,
    customer_name TEXT,
    status TEXT NOT NULL,          -- 'success' or 'error'
    source_file TEXT,
    output_file TEXT,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_log_timestamp ON conversion_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_log_invoice ON conversion_log(invoice_number);
"""


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock:
        conn = _connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()


def log_event(timestamp, profile_id, invoice_number, customer_name, status, source_file, output_file, message=""):
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO conversion_log "
                "(timestamp, profile_id, invoice_number, customer_name, status, source_file, output_file, message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (timestamp, profile_id, invoice_number, customer_name, status, source_file, output_file, message),
            )
            conn.commit()
        finally:
            conn.close()


def fetch_log(limit=200, status=None, search=None):
    with _lock:
        conn = _connect()
        try:
            query = "SELECT * FROM conversion_log"
            clauses = []
            params = []
            if status:
                clauses.append("status = ?")
                params.append(status)
            if search:
                clauses.append("(invoice_number LIKE ? OR customer_name LIKE ?)")
                like = f"%{search}%"
                params.extend([like, like])
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
