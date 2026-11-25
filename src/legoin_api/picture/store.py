"""Picture builds, picture jobs and what they spent, in SQLite (ADR-0012).

Their own file, `<data dir>/picture.db`, apart from the scans' database: the feature is
optional, and its tables don't change the scans' schema. One connection behind a lock, as in
`store.py`. Only the API process writes it; the worker process reports to it on its stdout.

- `picture_builds`: each saved build, by account.
- `picture_jobs`: each accepted job, by account and UTC day, and whether it counts toward the
  account's daily limit.
- `picture_spend`: every Claude call a job made, with its tokens and cost, by UTC day. The daily
  budget is their sum, so it survives restarts and deploys.
"""

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from legoin_api.picture.grid import Piece
from legoin_api.schemas import PartCount

SCHEMA_VERSION = 1

SCHEMA = f"""
BEGIN;
CREATE TABLE IF NOT EXISTS picture_builds (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    locale TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    steps TEXT NOT NULL,
    parts TEXT NOT NULL,
    pieces TEXT NOT NULL,
    picture_file TEXT NOT NULL,
    picture_width INTEGER NOT NULL,
    picture_height INTEGER NOT NULL,
    preview_file TEXT NOT NULL,
    model_file TEXT NOT NULL,
    plain INTEGER NOT NULL,
    model TEXT NOT NULL,
    cost REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS picture_builds_by_account ON picture_builds (account_id, created_at);

CREATE TABLE IF NOT EXISTS picture_jobs (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    day TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    code TEXT,
    counted INTEGER NOT NULL,
    cost REAL NOT NULL DEFAULT 0
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
PRAGMA user_version = {SCHEMA_VERSION};
COMMIT;
"""


@dataclass(frozen=True)
class StoredPictureBuild:
    id: str
    account_id: str
    created_at: str
    locale: str
    title: str
    summary: str
    steps: list[str]
    parts: list[PartCount]
    pieces: list[Piece]
    picture_file: str
    picture_width: int
    picture_height: int
    preview_file: str
    model_file: str
    plain: bool
    model: str
    cost: float

    @property
    def files(self) -> list[str]:
        return [self.picture_file, self.preview_file, self.model_file]


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


def _row_to_build(row: sqlite3.Row) -> StoredPictureBuild:
    return StoredPictureBuild(
        id=row["id"],
        account_id=row["account_id"],
        created_at=row["created_at"],
        locale=row["locale"],
        title=row["title"],
        summary=row["summary"],
        steps=json.loads(row["steps"]),
        parts=[PartCount.model_validate(line) for line in json.loads(row["parts"])],
        pieces=[Piece(**piece) for piece in json.loads(row["pieces"])],
        picture_file=row["picture_file"],
        picture_width=row["picture_width"],
        picture_height=row["picture_height"],
        preview_file=row["preview_file"],
        model_file=row["model_file"],
        plain=bool(row["plain"]),
        model=row["model"],
        cost=row["cost"],
    )


class PictureStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        if self._db.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # Builds

    def insert_build(self, build: StoredPictureBuild) -> None:
        with self._lock:
            self._db.execute(
                f"INSERT INTO picture_builds VALUES ({', '.join('?' * 17)})",
                (
                    build.id,
                    build.account_id,
                    build.created_at,
                    build.locale,
                    build.title,
                    build.summary,
                    json.dumps(build.steps, ensure_ascii=False),
                    json.dumps([line.wire() for line in build.parts]),
                    json.dumps([piece.wire() for piece in build.pieces]),
                    build.picture_file,
                    build.picture_width,
                    build.picture_height,
                    build.preview_file,
                    build.model_file,
                    int(build.plain),
                    build.model,
                    build.cost,
                ),
            )

    def get_build(self, account_id: str, build_id: str) -> StoredPictureBuild | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM picture_builds WHERE id = ? AND account_id = ?",
                (build_id, account_id),
            ).fetchone()
        return _row_to_build(row) if row else None

    def list_builds(self, account_id: str) -> list[StoredPictureBuild]:
        """This account's builds, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM picture_builds WHERE account_id = ?"
                " ORDER BY created_at DESC, id DESC",
                (account_id,),
            ).fetchall()
        return [_row_to_build(row) for row in rows]

    def delete_build(self, account_id: str, build_id: str) -> StoredPictureBuild | None:
        """Deletes one build and gives it back, so its files can go too."""
        with self._lock:
            row = self._db.execute(
                "DELETE FROM picture_builds WHERE id = ? AND account_id = ? RETURNING *",
                (build_id, account_id),
            ).fetchone()
        return _row_to_build(row) if row else None

    # Jobs and spending

    def insert_job(self, job_id: str, account_id: str, day: str, created_at: str) -> None:
        """A job that's been accepted: it counts toward the account's limit from now."""
        with self._lock:
            self._db.execute(
                "INSERT INTO picture_jobs (id, account_id, day, created_at, status, counted)"
                " VALUES (?, ?, ?, ?, 'running', 1)",
                (job_id, account_id, day, created_at),
            )

    def end_job(self, job_id: str, status: str, code: str | None, *, counted: bool) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE picture_jobs SET status = ?, code = ?, counted = ? WHERE id = ?",
                (status, code, int(counted), job_id),
            )

    def jobs_counted(self, account_id: str, day: str) -> int:
        """The account's jobs that count toward its limit on this UTC day."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM picture_jobs"
                " WHERE account_id = ? AND day = ? AND counted = 1",
                (account_id, day),
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
        """Dollars every picture job spent on this UTC day."""
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(SUM(cost), 0) FROM picture_spend WHERE day = ?", (day,)
            ).fetchone()
        return float(row[0])
