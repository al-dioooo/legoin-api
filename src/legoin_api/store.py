"""Scans in SQLite, keyed by the device that made them.

One connection, guarded by a lock: the API's request threads and the scan worker share it,
and every change is a short transaction. Detections and the latest idea set are stored as
JSON in the scan's row, since they're always read and written with the scan.
"""

import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from legoin_api.errors import ApiError
from legoin_api.ideas.generator import ShownIdea
from legoin_api.ids import utc_now
from legoin_api.inventory import recheck, to_inventory
from legoin_api.schemas import Detection, IdeaSet, PartCount, Photo, Scan, Theme

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
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
CREATE INDEX IF NOT EXISTS scans_by_device ON scans (device_id, created_at);

-- Every idea written for a scan, not only the latest set, so `exclude` can name older ones.
CREATE TABLE IF NOT EXISTS ideas_written (
    scan_id TEXT NOT NULL REFERENCES scans (id) ON DELETE CASCADE,
    idea_id TEXT NOT NULL,
    key TEXT NOT NULL,
    theme TEXT NOT NULL,
    title TEXT NOT NULL,
    written_at TEXT NOT NULL,
    PRIMARY KEY (scan_id, idea_id)
);
"""


@dataclass(frozen=True)
class StoredScan:
    id: str
    device_id: str
    created_at: str
    photo_file: str
    photo_width: int
    photo_height: int
    detections: list[Detection]
    last_theme: Theme | None = None
    ideas: IdeaSet | None = None

    def to_wire(self, photo_url: Callable[[str], str]) -> Scan:
        return Scan(
            id=self.id,
            created_at=self.created_at,
            photo=Photo(
                src=photo_url(self.photo_file), width=self.photo_width, height=self.photo_height
            ),
            detections=self.detections,
            last_theme=self.last_theme,
            ideas=self.ideas,
        )

    def with_changes(self, **changes) -> "StoredScan":
        return replace(self, **changes)


def _row_to_scan(row: sqlite3.Row) -> StoredScan:
    return StoredScan(
        id=row["id"],
        device_id=row["device_id"],
        created_at=row["created_at"],
        photo_file=row["photo_file"],
        photo_width=row["photo_width"],
        photo_height=row["photo_height"],
        detections=[Detection.model_validate(d) for d in json.loads(row["detections"])],
        last_theme=row["last_theme"],
        ideas=IdeaSet.model_validate_json(row["ideas"]) if row["ideas"] else None,
    )


def _detections_json(detections: list[Detection]) -> str:
    return json.dumps([d.wire() for d in detections])


def _ideas_json(ideas: IdeaSet | None) -> str | None:
    return ideas.model_dump_json(by_alias=True) if ideas else None


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def insert_scan(self, scan: StoredScan) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO scans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scan.id,
                    scan.device_id,
                    scan.created_at,
                    scan.photo_file,
                    scan.photo_width,
                    scan.photo_height,
                    _detections_json(scan.detections),
                    scan.last_theme,
                    _ideas_json(scan.ideas),
                ),
            )

    def get_scan(self, device_id: str, scan_id: str) -> StoredScan | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM scans WHERE id = ? AND device_id = ?", (scan_id, device_id)
            ).fetchone()
        return _row_to_scan(row) if row else None

    def list_scans(self, device_id: str) -> list[StoredScan]:
        """This device's scans, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM scans WHERE device_id = ? ORDER BY created_at DESC, id DESC",
                (device_id,),
            ).fetchall()
        return [_row_to_scan(row) for row in rows]

    def modify_scan(
        self, device_id: str, scan_id: str, change: Callable[[StoredScan], StoredScan]
    ) -> StoredScan | None:
        """Reads, changes and writes one scan in a single transaction. None if it's not there."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT * FROM scans WHERE id = ? AND device_id = ?", (scan_id, device_id)
                ).fetchone()
                if row is None:
                    self._db.execute("ROLLBACK")
                    return None
                scan = change(_row_to_scan(row))
                self._db.execute(
                    "UPDATE scans SET detections = ?, last_theme = ?, ideas = ? WHERE id = ?",
                    (
                        _detections_json(scan.detections),
                        scan.last_theme,
                        _ideas_json(scan.ideas),
                        scan.id,
                    ),
                )
                self._db.execute("COMMIT")
                return scan
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def written_ideas(self, device_id: str, scan_id: str) -> list[ShownIdea]:
        """Every idea written for one of this device's scans, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT i.* FROM ideas_written i JOIN scans s ON s.id = i.scan_id"
                " WHERE i.scan_id = ? AND s.device_id = ? ORDER BY i.written_at, i.rowid",
                (scan_id, device_id),
            ).fetchall()
        return [ShownIdea(row["idea_id"], row["key"], row["theme"], row["title"]) for row in rows]

    def keep_ideas(
        self,
        device_id: str,
        scan_id: str,
        ideas: IdeaSet,
        keys: list[str],
        checked_against: list[PartCount],
    ) -> StoredScan | None:
        """Keeps a new set as the scan's `ideas` and its theme as `lastTheme`, in one transaction,
        and gives the scan back with the set as kept. None if the scan is gone.

        If a correction changed the inventory while the ideas were being written, the builds
        still stand, but they were checked against pieces that have changed: `used` and
        `missing` are worked out again against the pieces as they are now, so the set that's
        answered is the set that's kept. With no pieces left, it's `no_pieces` and nothing
        changes.
        """
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT * FROM scans WHERE id = ? AND device_id = ?", (scan_id, device_id)
                ).fetchone()
                if row is None:
                    self._db.execute("ROLLBACK")
                    return None
                scan = _row_to_scan(row)
                inventory = to_inventory(scan.detections)
                if inventory != checked_against:
                    if not inventory:
                        raise ApiError("no_pieces", f"Scan {scan_id} has no pieces to build with.")
                    ideas = recheck(ideas, inventory)
                scan = scan.with_changes(last_theme=ideas.theme, ideas=ideas)
                now = utc_now()
                self._db.executemany(
                    "INSERT INTO ideas_written VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (scan_id, idea.id, key, idea.theme, idea.title, now)
                        for idea, key in zip(ideas.items, keys, strict=True)
                    ],
                )
                self._db.execute(
                    "UPDATE scans SET last_theme = ?, ideas = ? WHERE id = ?",
                    (scan.last_theme, _ideas_json(scan.ideas), scan_id),
                )
                self._db.execute("COMMIT")
                return scan
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def delete_scan(self, device_id: str, scan_id: str) -> StoredScan | None:
        """Deletes one scan and gives it back, so its photo can go too. None if it wasn't there."""
        with self._lock:
            row = self._db.execute(
                "DELETE FROM scans WHERE id = ? AND device_id = ? RETURNING *", (scan_id, device_id)
            ).fetchone()
        return _row_to_scan(row) if row else None
