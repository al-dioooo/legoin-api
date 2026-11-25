"""An ADR-0008 data directory is migrated to accounts on start (ADR-0011)."""

import sqlite3

from legoin_api.store import SCHEMA_VERSION, Store

VERSION_1 = """
CREATE TABLE scans (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    photo_file TEXT NOT NULL,
    photo_width INTEGER NOT NULL,
    photo_height INTEGER NOT NULL,
    detections TEXT NOT NULL,
    last_theme TEXT,
    ideas TEXT
);
CREATE INDEX scans_by_device ON scans (device_id, created_at);
CREATE TABLE ideas_written (
    scan_id TEXT NOT NULL REFERENCES scans (id) ON DELETE CASCADE,
    idea_id TEXT NOT NULL,
    key TEXT NOT NULL,
    theme TEXT NOT NULL,
    title TEXT NOT NULL,
    written_at TEXT NOT NULL,
    PRIMARY KEY (scan_id, idea_id)
);
INSERT INTO scans VALUES
    ('scn_1', 'device-a', '2026-11-09T03:12:45.120Z', 'a.jpg', 10, 20, '[]', 'vehicles', NULL);
INSERT INTO ideas_written VALUES
    ('scn_1', 'idea_1', 'race-car', 'vehicles', 'Race car', '2026-11-09T03:13:00.000Z');
"""
"""The schema legoin-api created before accounts, as ADR-0008 shipped it."""


def test_a_version_1_file_keeps_its_scans_and_ideas(tmp_path):
    path = tmp_path / "legoin.db"
    db = sqlite3.connect(path)
    db.executescript(VERSION_1)
    db.close()

    store = Store(path)
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    scan = store._db.execute("SELECT * FROM scans").fetchone()
    assert (scan["id"], scan["device_id"], scan["account_id"]) == ("scn_1", "device-a", None)
    assert (scan["photo_width"], scan["last_theme"]) == (10, "vehicles")
    assert store._db.execute("SELECT idea_id FROM ideas_written").fetchall()[0][0] == "idea_1"
    # Foreign keys are back on, and still point at the new scans table.
    assert store._db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    store._db.execute("DELETE FROM scans WHERE id = 'scn_1'")
    assert store._db.execute("SELECT COUNT(*) FROM ideas_written").fetchone()[0] == 0
    store.close()


def test_a_migrated_file_is_left_alone_next_time(tmp_path):
    path = tmp_path / "legoin.db"
    db = sqlite3.connect(path)
    db.executescript(VERSION_1)
    db.close()
    Store(path).close()

    store = Store(path)
    assert store._db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 1
    store.close()


def test_a_new_file_starts_at_the_current_version(tmp_path):
    store = Store(tmp_path / "legoin.db")
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    tables = {
        row[0] for row in store._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"accounts", "sessions", "scans", "ideas_written"} <= tables
    store.close()
