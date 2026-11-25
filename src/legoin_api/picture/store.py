"""Build jobs, what they spent, and the builds of ideas, in SQLite (ADR-0013).

Their own file, `<data dir>/picture.db`, apart from the scans' database: the feature is
optional, and its tables don't change the scans' schema. One connection behind a lock, as in
`store.py`. Only the API process writes it; the worker process reports to it on its stdout.

- `picture_jobs`: each accepted job, by account and UTC day, its `kind`, and whether it counts
  toward the account's daily limit. A set of builds from a scan is one job of kind `scan`.
- `picture_spend`: every Claude call a job made, with its tokens and cost, by UTC day. The daily
  budget is their sum, so it survives restarts and deploys.
- `idea_builds`: the builds Claude made of ideas, by account and scan, with their files.

Version 2 (ADR-0012) added previews of ideas: a job's `kind` and `idea_builds`. Version 3
(ADR-0013) drops `picture_builds`, the standalone picture builds, and their files: their page
and routes are gone, so their owners couldn't see or delete them any more. A version 1 or 2
file is migrated in place on start.
"""

import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from legoin_api.picture.grid import Piece

SCHEMA_VERSION = 3

IDEA_BUILDS = """
CREATE TABLE IF NOT EXISTS idea_builds (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    idea_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    preview_file TEXT NOT NULL,
    model_file TEXT NOT NULL,
    pieces TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idea_builds_by_scan ON idea_builds (account_id, scan_id);
"""

MIGRATE_FROM_1 = f"""
BEGIN;
ALTER TABLE picture_jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'picture';
{IDEA_BUILDS}
PRAGMA user_version = 2;
COMMIT;
"""

MIGRATE_FROM_2 = f"""
BEGIN;
DROP TABLE IF EXISTS picture_builds;
PRAGMA user_version = {SCHEMA_VERSION};
COMMIT;
"""

SCHEMA = f"""
BEGIN;
CREATE TABLE IF NOT EXISTS picture_jobs (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    day TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    code TEXT,
    counted INTEGER NOT NULL,
    cost REAL NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'scan'
);
CREATE INDEX IF NOT EXISTS picture_jobs_by_day ON picture_jobs (day, account_id);

CREATE TABLE IF NOT EXISTS picture_spend (
    job_id TEXT NOT NULL,
    day TEXT NOT NULL,
    at TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    cost REAL NOT NULL,
    estimated INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS picture_spend_by_day ON picture_spend (day);
{IDEA_BUILDS}
PRAGMA user_version = {SCHEMA_VERSION};
COMMIT;
"""


@dataclass(frozen=True)
class Spend:
    """One Claude call: its tokens and dollars. `estimated` when the worker was stopped
    during the call, so its worst case is counted instead."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0.0
    estimated: bool = False


class PictureStore:
    def __init__(self, path: Path, delete_files: Callable[[list[str]], None] = lambda names: None):
        """`delete_files` deletes the files of the standalone picture builds that a version 1
        or 2 file still has, before their rows go."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            self._db.executescript(SCHEMA)
            return
        if version == 1:
            self._db.executescript(MIGRATE_FROM_1)
        if version <= 2:
            rows = self._db.execute(
                "SELECT picture_file, preview_file, model_file FROM picture_builds"
            ).fetchall()
            delete_files([name for row in rows for name in row])
            self._db.executescript(MIGRATE_FROM_2)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # Jobs and spending

    def insert_job(
        self, job_id: str, account_id: str, day: str, created_at: str, kind: str = "scan"
    ) -> None:
        """A job that's been accepted: it counts toward the account's limit from now."""
        with self._lock:
            self._db.execute(
                "INSERT INTO picture_jobs (id, account_id, day, created_at, status, counted, kind)"
                " VALUES (?, ?, ?, ?, 'running', 1, ?)",
                (job_id, account_id, day, created_at, kind),
            )

    def end_job(self, job_id: str, status: str, code: str | None, *, counted: bool) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE picture_jobs SET status = ?, code = ?, counted = ? WHERE id = ?",
                (status, code, int(counted), job_id),
            )

    def jobs_counted(self, account_id: str, day: str, kind: str = "scan") -> int:
        """The account's jobs of this kind that count toward its limit on this UTC day."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM picture_jobs"
                " WHERE account_id = ? AND day = ? AND counted = 1 AND kind = ?",
                (account_id, day, kind),
            ).fetchone()
        return row[0]

    def record_spend(self, job_id: str, day: str, at: str, spend: Spend) -> None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "INSERT INTO picture_spend VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        day,
                        at,
                        spend.model,
                        spend.input_tokens,
                        spend.output_tokens,
                        spend.cache_read_tokens,
                        spend.cache_write_tokens,
                        spend.cost,
                        int(spend.estimated),
                    ),
                )
                self._db.execute(
                    "UPDATE picture_jobs SET cost = cost + ? WHERE id = ?", (spend.cost, job_id)
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def spent(self, day: str) -> float:
        """Dollars every job spent on this UTC day."""
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(SUM(cost), 0) FROM picture_spend WHERE day = ?", (day,)
            ).fetchone()
        return float(row[0])

    # Builds of ideas

    def insert_idea_build(self, build: "StoredIdeaBuild") -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO idea_builds VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    build.id,
                    build.account_id,
                    build.scan_id,
                    build.idea_id,
                    build.created_at,
                    build.preview_file,
                    build.model_file,
                    json.dumps([piece.wire() for piece in build.pieces]),
                ),
            )

    def get_idea_build(self, build_id: str) -> "StoredIdeaBuild | None":
        """A build by its id, which is random: the idea that names it is the account's."""
        with self._lock:
            row = self._db.execute("SELECT * FROM idea_builds WHERE id = ?", (build_id,)).fetchone()
        return _row_to_idea_build(row) if row else None

    def delete_idea_build(self, build_id: str) -> "StoredIdeaBuild | None":
        with self._lock:
            row = self._db.execute(
                "DELETE FROM idea_builds WHERE id = ? RETURNING *", (build_id,)
            ).fetchone()
        return _row_to_idea_build(row) if row else None

    def delete_idea_builds(
        self, account_id: str, scan_id: str, keep: frozenset[str] = frozenset()
    ) -> list["StoredIdeaBuild"]:
        """Deletes a scan's builds, except those of the ideas in `keep`, and gives them back so
        their files can go too."""
        with self._lock:
            rows = self._db.execute(
                "DELETE FROM idea_builds WHERE account_id = ? AND scan_id = ? RETURNING *",
                (account_id, scan_id),
            ).fetchall()
            kept = [row for row in rows if row["idea_id"] in keep]
            for row in kept:
                self._db.execute(
                    "INSERT INTO idea_builds VALUES (?, ?, ?, ?, ?, ?, ?, ?)", tuple(row)
                )
        return [_row_to_idea_build(row) for row in rows if row["idea_id"] not in keep]


@dataclass(frozen=True)
class StoredIdeaBuild:
    """The build Claude made of an idea: its picture and model, and its pieces."""

    id: str
    account_id: str
    scan_id: str
    idea_id: str
    created_at: str
    preview_file: str
    model_file: str
    pieces: list[Piece]

    @property
    def files(self) -> list[str]:
        return [self.preview_file, self.model_file]


def _row_to_idea_build(row: sqlite3.Row) -> StoredIdeaBuild:
    return StoredIdeaBuild(
        id=row["id"],
        account_id=row["account_id"],
        scan_id=row["scan_id"],
        idea_id=row["idea_id"],
        created_at=row["created_at"],
        preview_file=row["preview_file"],
        model_file=row["model_file"],
        pieces=[Piece(**piece) for piece in json.loads(row["pieces"])],
    )
