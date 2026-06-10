"""Tests for the SQLite migration runner and startup hook."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from migrations.runner import run_migrations


def test_runner_applies_sql_files(tmp_path):
    """Runner should execute sorted .sql files and track them in _migrations."""
    db = tmp_path / "polstock_backend.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_create.sql").write_text(
        "CREATE TABLE IF NOT EXISTS t1 (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    run_migrations(str(db), str(migrations_dir))

    conn = sqlite3.connect(str(db))
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()

    assert "t1" in tables
    assert "_migrations" in tables


def test_runner_is_idempotent(tmp_path):
    """Running migrations twice should not fail or duplicate schema."""
    db = tmp_path / "polstock_backend.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_create.sql").write_text(
        "CREATE TABLE IF NOT EXISTS t1 (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    run_migrations(str(db), str(migrations_dir))
    # Second run must not raise
    applied = run_migrations(str(db), str(migrations_dir))

    # Already applied, so nothing new
    assert applied == []

    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    conn.close()
    assert count == 1


def test_runner_skips_already_applied(tmp_path):
    """Runner should only apply new migrations."""
    db = tmp_path / "polstock_backend.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_first.sql").write_text(
        "CREATE TABLE IF NOT EXISTS t1 (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )

    applied1 = run_migrations(str(db), str(migrations_dir))
    assert "001_first.sql" in applied1

    # Add a second migration
    (migrations_dir / "002_second.sql").write_text(
        "CREATE TABLE IF NOT EXISTS t2 (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )

    applied2 = run_migrations(str(db), str(migrations_dir))
    assert "002_second.sql" in applied2
    assert "001_first.sql" not in applied2


def test_startup_runs_migrations(tmp_path, monkeypatch):
    """run_startup_migrations() should apply the baseline schema snapshot."""
    db = tmp_path / "polstock_backend.db"
    monkeypatch.setenv("POLSTOCK_BACKEND_DB", str(db))

    # We need to reload config so BACKEND_DB_PATH picks up the env change.
    # The import trick: reimport with updated env.
    import importlib
    import backend.config as cfg
    monkeypatch.setattr(cfg, "BACKEND_DB_PATH", db)

    import backend.main as appmod
    monkeypatch.setattr(appmod, "BACKEND_DB_PATH", db)
    appmod.run_startup_migrations()

    conn = sqlite3.connect(str(db))
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()

    # All production tables should exist
    expected = {
        "source_outcomes",
        "events_cache",
        "predictions",
        "historical_events",
        "source_accuracy",
        "signal_history",
        "portfolio",
        "pinned_tickers",
        "daily_signal_snapshots",
        "alert_dedup",
        "_migrations",
    }
    assert expected <= tables, f"Missing tables: {expected - tables}"


def test_startup_migrations_idempotent(tmp_path, monkeypatch):
    """Running startup migrations twice must not fail."""
    db = tmp_path / "polstock_backend.db"
    monkeypatch.setenv("POLSTOCK_BACKEND_DB", str(db))

    import backend.config as cfg
    monkeypatch.setattr(cfg, "BACKEND_DB_PATH", db)

    import backend.main as appmod
    monkeypatch.setattr(appmod, "BACKEND_DB_PATH", db)
    appmod.run_startup_migrations()
    # Second call must not raise
    appmod.run_startup_migrations()

    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    conn.close()
    assert count == 1
