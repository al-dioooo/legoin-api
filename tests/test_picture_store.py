import pytest

from legoin_api.config import Settings
from legoin_api.picture import availability
from legoin_api.picture.files import PictureFiles
from legoin_api.picture.grid import Piece
from legoin_api.picture.store import PictureStore, Spend


@pytest.fixture
def store(tmp_path):
    store = PictureStore(tmp_path / "picture.db")
    yield store
    store.close()


def test_spending_and_jobs_are_counted_by_day_and_kept(store, tmp_path):
    store.insert_job("pjob_1", "acc_a", "2026-11-25", "t")
    store.insert_job("pjob_2", "acc_a", "2026-11-25", "t")
    store.insert_job("pjob_3", "acc_a", "2026-11-24", "t")
    store.end_job("pjob_2", "failed", "unavailable", counted=False)
    store.record_spend("pjob_1", "2026-11-25", "t", Spend("claude-opus-5-5", 8, 4000, cost=0.12))
    store.record_spend(
        "pjob_1", "2026-11-25", "t", Spend("claude-opus-5-5", cost=0.3, estimated=True)
    )
    store.record_spend("pjob_3", "2026-11-24", "t", Spend("claude-opus-5-5", cost=1.0))
    store.close()

    again = PictureStore(tmp_path / "picture.db")
    assert again.jobs_counted("acc_a", "2026-11-25") == 1
    assert again.jobs_counted("acc_b", "2026-11-25") == 0
    assert again.spent("2026-11-25") == pytest.approx(0.42)
    assert again.spent("2026-11-26") == 0
    again.close()


def test_file_names_are_random_and_only_the_stores_own_are_served(tmp_path):
    files = PictureFiles(tmp_path / "files")
    source = tmp_path / "preview.webp"
    source.write_bytes(b"webp")

    name = files.adopt(source, "webp")

    assert len(name) == 27 and name.endswith(".webp")
    assert files.path(name).read_bytes() == b"webp"
    assert not source.exists()
    for bad in ("../picture.db", "x.jpg", "a" * 22 + ".png", "a" * 22 + ".ldr/../x"):
        assert files.path(bad) is None
    files.delete(name)
    assert not files.path(name).exists()


def test_the_defaults_are_the_adrs():
    settings = Settings()

    assert settings.scan_builds == "off"
    assert (settings.scan_build_model, settings.scan_build_effort) == ("claude-opus-5-5", "medium")
    assert settings.scan_build_daily_budget_usd == 3.0
    assert settings.scan_build_daily_sets == 3
    assert settings.scan_build_cost_usd == 0.35
    assert (settings.scan_build_seconds, settings.scan_build_calls) == (150, 12)
    assert settings.scan_build_stagger_s == 5.0
    assert settings.scan_build_worker_memory_mb == 1024


def test_builds_are_off_until_everything_is_there(monkeypatch, tmp_path):
    assert availability.missing(Settings()) == ["LEGOIN_SCAN_BUILDS is off"]

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LDRAW_LIBRARY_PATH", str(tmp_path))
    monkeypatch.setattr(availability.importlib.util, "find_spec", lambda name: None)
    missing = availability.missing(Settings(scan_builds="anthropic"))

    assert missing[0] == "ANTHROPIC_API_KEY is unset"
    assert missing[1].startswith("the picture extra isn't installed (bricklayer, ")
    assert missing[2] == "no LDraw parts library: set LDRAW_LIBRARY_PATH"

    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "3001.dat").write_text("0 Brick 2 x 4\n")
    assert availability.ldraw_library() == tmp_path


VERSION_2 = """
CREATE TABLE picture_builds (
    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, created_at TEXT NOT NULL,
    locale TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, steps TEXT NOT NULL,
    parts TEXT NOT NULL, pieces TEXT NOT NULL, picture_file TEXT NOT NULL,
    picture_width INTEGER NOT NULL, picture_height INTEGER NOT NULL,
    preview_file TEXT NOT NULL, model_file TEXT NOT NULL, plain INTEGER NOT NULL,
    model TEXT NOT NULL, cost REAL NOT NULL
);
CREATE TABLE picture_jobs (
    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, day TEXT NOT NULL, created_at TEXT NOT NULL,
    status TEXT NOT NULL, code TEXT, counted INTEGER NOT NULL, cost REAL NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'picture'
);
CREATE TABLE picture_spend (
    job_id TEXT NOT NULL, day TEXT NOT NULL, at TEXT NOT NULL, model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL, cache_write_tokens INTEGER NOT NULL,
    cost REAL NOT NULL, estimated INTEGER NOT NULL
);
"""
"""ADR-0012's tables, as a version 1 file had them, less the job's `kind`."""


@pytest.mark.parametrize("version", [1, 2])
def test_an_older_file_drops_the_picture_builds_and_their_files(tmp_path, version):
    import sqlite3

    from legoin_api.picture import store as module

    path = tmp_path / "picture.db"
    old = sqlite3.connect(path)
    schema = VERSION_2 + (module.IDEA_BUILDS if version == 2 else "")
    if version == 1:
        schema = schema.replace(",\n    kind TEXT NOT NULL DEFAULT 'picture'", "")
    old.executescript(schema + f"PRAGMA user_version = {version};")
    old.execute(
        "INSERT INTO picture_builds VALUES ('pic_1', 'acc_a', 't', 'en', 'T', 'S', '[]', '[]',"
        " '[]', 'p.jpg', 900, 600, 'p.webp', 'p.ldr', 0, 'm', 0.1)"
    )
    old.execute(
        "INSERT INTO picture_jobs (id, account_id, day, created_at, status, counted)"
        " VALUES ('pjob_old', 'acc_a', '2026-11-25', 't', 'done', 1)"
    )
    old.execute(
        "INSERT INTO picture_spend VALUES ('pjob_old', '2026-11-25', 't', 'm', 0, 0, 0, 0, 0.4, 0)"
    )
    old.commit()
    old.close()
    deleted: list[str] = []

    store = PictureStore(path, delete_files=deleted.extend)

    assert deleted == ["p.jpg", "p.webp", "p.ldr"]
    tables = {row[0] for row in store._db.execute("SELECT name FROM sqlite_master")}
    assert "picture_builds" not in tables and "idea_builds" in tables
    # The day's spending still counts toward the budget; old picture jobs aren't sets.
    assert store.spent("2026-11-25") == pytest.approx(0.4)
    assert store.jobs_counted("acc_a", "2026-11-25") == 0
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 3
    store.close()
    # Once is enough.
    PictureStore(path, delete_files=deleted.extend).close()
    assert len(deleted) == 3


def idea_build(build_id, idea_id, scan_id="scn_1", account="acc_a"):
    from legoin_api.picture.store import StoredIdeaBuild

    return StoredIdeaBuild(
        id=build_id,
        account_id=account,
        scan_id=scan_id,
        idea_id=idea_id,
        created_at="t",
        preview_file=f"{build_id:x<22}.webp",
        model_file=f"{build_id:x<22}.ldr",
        pieces=[Piece("p1", "brick-2x4", "red", 0, 0, 0, 0)],
    )


def test_idea_builds_are_kept_by_scan_and_deleted_but_those_kept(store):
    for build_id, idea_id in [("gen_1", "idea_a"), ("gen_2", "idea_b"), ("gen_3", "idea_c")]:
        store.insert_idea_build(idea_build(build_id, idea_id))
    store.insert_idea_build(idea_build("gen_4", "idea_a", scan_id="scn_2"))

    gone = store.delete_idea_builds("acc_a", "scn_1", keep=frozenset({"idea_b"}))

    assert sorted(b.id for b in gone) == ["gen_1", "gen_3"]
    assert store.get_idea_build("gen_2") == idea_build("gen_2", "idea_b")
    assert store.get_idea_build("gen_1") is None
    assert store.get_idea_build("gen_4") is not None
    assert store.delete_idea_build("gen_4").idea_id == "idea_a"
    store.insert_job("bjob_1", "acc_a", "2026-11-25", "t")
    store.insert_job("pjob_i", "acc_a", "2026-11-25", "t", kind="idea")
    assert store.jobs_counted("acc_a", "2026-11-25") == 1
    assert store.jobs_counted("acc_a", "2026-11-25", "idea") == 1
