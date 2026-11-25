"""Accounts, sessions and scans in SQLite.

One connection, guarded by a lock: the API's request threads and the scan worker share it,
and every change is a short transaction. Detections and the latest idea set are stored as
JSON in the scan's row, since they're always read and written with the scan.

The file's `user_version` is its schema's version. Version 1 (ADR-0008) kept scans by device
only; version 2 (ADR-0011) adds accounts and sessions, and a scan's device id becomes optional:
it's kept only until an account claims the scan. An older file is migrated on start.
"""

import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from legoin_api.errors import ApiError
from legoin_api.ideas.generator import ShownIdea
from legoin_api.ids import utc_now
from legoin_api.inventory import recheck, to_inventory
from legoin_api.schemas import Account, Detection, IdeaSet, PartCount, Photo, Scan, Theme

SCHEMA_VERSION = 2

ACCOUNTS = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Only a SHA-256 of each token, so a copy of the file can't be used to log in.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_account ON sessions (account_id);
"""


def _scans_table(name: str) -> str:
    """The scans table: an account's, or a device's until an account claims it."""
    return f"""
CREATE TABLE {name} (
    id TEXT PRIMARY KEY,
    account_id TEXT REFERENCES accounts (id),
    device_id TEXT,
    created_at TEXT NOT NULL,
    photo_file TEXT NOT NULL,
    photo_width INTEGER NOT NULL,
    photo_height INTEGER NOT NULL,
    detections TEXT NOT NULL,
    last_theme TEXT,
    ideas TEXT
);
"""


SCAN_INDEXES = """
CREATE INDEX IF NOT EXISTS scans_by_account ON scans (account_id, created_at);
CREATE INDEX IF NOT EXISTS scans_by_device ON scans (device_id, created_at);
"""

IDEAS_WRITTEN = """
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

SCAN_COLUMNS = (
    "id, account_id, device_id, created_at, photo_file, photo_width, photo_height,"
    " detections, last_theme, ideas"
)

MIGRATE_FROM_1 = f"""
BEGIN;
{ACCOUNTS}
{_scans_table("scans_v2")}
INSERT INTO scans_v2 ({SCAN_COLUMNS})
    SELECT id, NULL, device_id, created_at, photo_file, photo_width, photo_height,
        detections, last_theme, ideas
    FROM scans;
DROP TABLE scans;
ALTER TABLE scans_v2 RENAME TO scans;
{SCAN_INDEXES}
PRAGMA user_version = {SCHEMA_VERSION};
COMMIT;
"""
"""SQLite can't make a column optional in place, so the scans table is copied, as its
documentation's twelve steps do it. Foreign keys are off meanwhile, so dropping the old table
doesn't take the scans' ideas with it."""


def _migrate(db: sqlite3.Connection) -> None:
    """Brings the file to `SCHEMA_VERSION`: a new file gets every table, a version 1 file is
    copied over, in one transaction."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    has_scans = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'scans'"
    ).fetchone()
    if not has_scans:
        db.executescript(
            f"BEGIN; {ACCOUNTS} {_scans_table('scans')} {SCAN_INDEXES} {IDEAS_WRITTEN}"
            f" PRAGMA user_version = {SCHEMA_VERSION}; COMMIT;"
        )
        return
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.executescript(MIGRATE_FROM_1)
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.execute("PRAGMA foreign_keys = ON")
    if db.execute("PRAGMA foreign_key_check").fetchone():
        raise RuntimeError("Migrating the scans table left a dangling foreign key.")


class WireUrls(Protocol):
    """What a scan needs to go on the wire: its photo's URL and its ideas' previews."""

    def photo(self, name: str) -> str: ...

    def with_previews(self, ideas: IdeaSet) -> IdeaSet: ...


@dataclass(frozen=True)
class StoredScan:
    id: str
    account_id: str
    created_at: str
    photo_file: str
    photo_width: int
    photo_height: int
    detections: list[Detection]
    last_theme: Theme | None = None
    ideas: IdeaSet | None = None

    def to_wire(self, urls: "WireUrls") -> Scan:
        return Scan(
            id=self.id,
            created_at=self.created_at,
            photo=Photo(
                src=urls.photo(self.photo_file), width=self.photo_width, height=self.photo_height
            ),
            detections=self.detections,
            last_theme=self.last_theme,
            ideas=urls.with_previews(self.ideas) if self.ideas else None,
        )

    def with_changes(self, **changes) -> "StoredScan":
        return replace(self, **changes)


@dataclass(frozen=True)
class StoredAccount:
    id: str
    email: str
    password_hash: str
    created_at: str

    def to_wire(self) -> Account:
        return Account(id=self.id, email=self.email, created_at=self.created_at)


def _row_to_account(row: sqlite3.Row) -> StoredAccount:
    return StoredAccount(row["id"], row["email"], row["password_hash"], row["created_at"])


def _row_to_scan(row: sqlite3.Row) -> StoredScan:
    return StoredScan(
        id=row["id"],
        account_id=row["account_id"],
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
        _migrate(self._db)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def insert_scan(self, scan: StoredScan) -> None:
        with self._lock:
            self._db.execute(
                f"INSERT INTO scans ({SCAN_COLUMNS}) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scan.id,
                    scan.account_id,
                    scan.created_at,
                    scan.photo_file,
                    scan.photo_width,
                    scan.photo_height,
                    _detections_json(scan.detections),
                    scan.last_theme,
                    _ideas_json(scan.ideas),
                ),
            )

    def get_scan(self, account_id: str, scan_id: str) -> StoredScan | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM scans WHERE id = ? AND account_id = ?", (scan_id, account_id)
            ).fetchone()
        return _row_to_scan(row) if row else None

    def list_scans(self, account_id: str) -> list[StoredScan]:
        """This account's scans, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM scans WHERE account_id = ? ORDER BY created_at DESC, id DESC",
                (account_id,),
            ).fetchall()
        return [_row_to_scan(row) for row in rows]

    def modify_scan(
        self, account_id: str, scan_id: str, change: Callable[[StoredScan], StoredScan]
    ) -> StoredScan | None:
        """Reads, changes and writes one scan in a single transaction. None if it's not there."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT * FROM scans WHERE id = ? AND account_id = ?", (scan_id, account_id)
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

    def written_ideas(self, account_id: str, scan_id: str) -> list[ShownIdea]:
        """Every idea written for one of this account's scans, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT i.* FROM ideas_written i JOIN scans s ON s.id = i.scan_id"
                " WHERE i.scan_id = ? AND s.account_id = ? ORDER BY i.written_at, i.rowid",
                (scan_id, account_id),
            ).fetchall()
        return [ShownIdea(row["idea_id"], row["key"], row["theme"], row["title"]) for row in rows]

    def keep_ideas(
        self,
        account_id: str,
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
                    "SELECT * FROM scans WHERE id = ? AND account_id = ?", (scan_id, account_id)
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

    def delete_scan(self, account_id: str, scan_id: str) -> StoredScan | None:
        """Deletes one scan and gives it back, so its photo can go too. None if it wasn't there."""
        with self._lock:
            row = self._db.execute(
                "DELETE FROM scans WHERE id = ? AND account_id = ? RETURNING *",
                (scan_id, account_id),
            ).fetchone()
        return _row_to_scan(row) if row else None

    # Accounts and sessions

    def insert_account(self, account: StoredAccount) -> bool:
        """Adds an account. False if its email already has one."""
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO accounts VALUES (?, ?, ?, ?)",
                    (account.id, account.email, account.password_hash, account.created_at),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def account_by_email(self, email: str) -> StoredAccount | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
        return _row_to_account(row) if row else None

    def set_password_hash(self, account_id: str, password_hash: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE accounts SET password_hash = ? WHERE id = ?", (password_hash, account_id)
            )

    def insert_session(self, token_hash: str, account_id: str, expires_at: str) -> None:
        """Adds a session, and drops the account's sessions that have expired."""
        now = utc_now()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "DELETE FROM sessions WHERE account_id = ? AND expires_at <= ?",
                    (account_id, now),
                )
                self._db.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                    (token_hash, account_id, now, expires_at),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def session_account(self, token_hash: str) -> StoredAccount | None:
        """The account of a session that hasn't expired. An expired one is deleted."""
        now = utc_now()
        with self._lock:
            row = self._db.execute(
                "SELECT a.*, s.expires_at FROM sessions s JOIN accounts a ON a.id = s.account_id"
                " WHERE s.token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is not None and row["expires_at"] <= now:
                self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                row = None
        return _row_to_account(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def claim_device_scans(self, device_id: str, account_id: str) -> int:
        """Moves the scans saved under a device id before accounts, that no account has yet,
        to this account (ADR-0011). Answers how many moved."""
        with self._lock:
            moved = self._db.execute(
                "UPDATE scans SET account_id = ?, device_id = NULL"
                " WHERE device_id = ? AND account_id IS NULL",
                (account_id, device_id),
            )
        return moved.rowcount
