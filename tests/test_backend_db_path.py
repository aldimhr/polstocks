from backend import config
from backend import signals as signalsmod
from backend.signals import init_signal_tables, log_signal


def test_backend_db_path_env_isolation(monkeypatch, tmp_path):
    db_path = tmp_path / "polstock_backend.db"
    monkeypatch.setenv("POLSTOCK_BACKEND_DB", str(db_path))
    monkeypatch.setattr(config, "BACKEND_DB_PATH", db_path)
    monkeypatch.setattr(signalsmod, "BACKEND_DB_PATH", db_path)
    init_signal_tables()
    record = log_signal("BBCA.JK", "BUY", 0.6, 9000)
    assert record is not None
    assert db_path.exists()
