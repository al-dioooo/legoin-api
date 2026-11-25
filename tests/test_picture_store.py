import pytest

from legoin_api.config import Settings
from legoin_api.picture import availability
from legoin_api.picture.files import PictureFiles
from legoin_api.picture.grid import Piece
from legoin_api.picture.store import PictureStore, Spend, StoredPictureBuild
from legoin_api.schemas import PartCount


def build(build_id="pic_1", account="acc_a", created_at="2026-11-25T08:00:00.000Z"):
    return StoredPictureBuild(
        id=build_id,
        account_id=account,
        created_at=created_at,
        locale="id",
        title="Rumah kecil",
        summary="Rumah putih beratap merah.",
        steps=["Satu.", "Dua."],
        parts=[PartCount(part="brick-2x4", color="white", quantity=2)],
        pieces=[Piece("p1", "brick-2x4", "white", 0, 0, 0, 0)],
        picture_file="a" * 22 + ".jpg",
        picture_width=900,
        picture_height=600,
        preview_file="b" * 22 + ".webp",
        model_file="c" * 22 + ".ldr",
        plain=False,
        model="claude-opus-5-5",
        cost=0.14,
    )


@pytest.fixture
def store(tmp_path):
    store = PictureStore(tmp_path / "picture.db")
    yield store
    store.close()


def test_builds_belong_to_their_account(store):
    store.insert_build(build("pic_old", created_at="2026-11-24T08:00:00.000Z"))
    store.insert_build(build("pic_new"))
    store.insert_build(build("pic_other", account="acc_b"))

    assert [b.id for b in store.list_builds("acc_a")] == ["pic_new", "pic_old"]
    assert store.get_build("acc_a", "pic_other") is None
    assert store.get_build("acc_a", "pic_new") == build("pic_new")
    assert store.delete_build("acc_b", "pic_new") is None
    assert store.delete_build("acc_a", "pic_new").files == [
        "a" * 22 + ".jpg",
        "b" * 22 + ".webp",
        "c" * 22 + ".ldr",
    ]


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

    assert settings.picture_builds == "off"
    assert (settings.picture_model, settings.picture_effort) == ("claude-opus-5-5", "medium")
    assert settings.picture_daily_budget_usd == 3.0
    assert settings.picture_daily_limit == 2
    assert settings.picture_job_cost_usd == 0.5
    assert settings.picture_job_seconds == 300
    assert settings.picture_worker_memory_mb == 1024


def test_picture_builds_are_off_until_everything_is_there(monkeypatch, tmp_path):
    assert availability.missing(Settings()) == ["LEGOIN_PICTURE_BUILDS is off"]

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LDRAW_LIBRARY_PATH", str(tmp_path))
    monkeypatch.setattr(availability.importlib.util, "find_spec", lambda name: None)
    missing = availability.missing(Settings(picture_builds="anthropic"))

    assert missing[0] == "ANTHROPIC_API_KEY is unset"
    assert missing[1].startswith("the picture extra isn't installed (bricklayer, ")
    assert missing[2] == "no LDraw parts library: set LDRAW_LIBRARY_PATH"

    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "3001.dat").write_text("0 Brick 2 x 4\n")
    assert availability.ldraw_library() == tmp_path
