"""Lightweight SQLite migration runner.

Applies sorted ``.sql`` files from a migrations directory against a SQLite
database, tracking which files have already been executed in a
``_migrations`` table so that repeated runs are idempotent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def run_migrations(db_path: str, migrations_dir: str = "migrations") -> list[str]:
    """Apply pending ``.sql`` migration files to *db_path*.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file (created if absent).
    migrations_dir:
        Directory containing ``.sql`` migration files sorted by filename.

    Returns
    -------
    list[str]
        Filenames of migrations that were applied during this call.
    """
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)

    mdir = Path(migrations_dir)
    if not mdir.is_dir():
        return []

    # Collect and sort migration files
    sql_files = sorted(mdir.glob("*.sql"))
    if not sql_files:
        return []

    conn = sqlite3.connect(str(db), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    try:
        # Ensure tracking table exists
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _migrations ("
            "  filename TEXT PRIMARY KEY,"
            "  applied_at TEXT DEFAULT (datetime('now'))"
            ")"
        )
        conn.commit()

        # Load already-applied filenames
        applied = {
            row[0]
            for row in conn.execute("SELECT filename FROM _migrations").fetchall()
        }

        newly_applied: list[str] = []
        for sql_file in sql_files:
            if sql_file.name in applied:
                continue

            sql = sql_file.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO _migrations (filename) VALUES (?)", (sql_file.name,)
            )
            conn.commit()
            newly_applied.append(sql_file.name)

        return newly_applied
    finally:
        conn.close()
