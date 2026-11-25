import hashlib
import json
from pathlib import Path

import pytest

from legoin_api.catalogue import DESIGN_IDS, PART_IDS
from legoin_api.config import Settings
from legoin_api.pipeline.release import TEMPERATURE, check_label_map
from legoin_api.weights import MANIFESTS, ModelFileError, ensure_models

# The SHA-256 of each file, as legoin-ml v0.2.0's release notes list them.
RELEASE_NOTES_V0_2_0 = {
    "best.pt": "60f5b3a881b2a289aa2de86f7e1695cb68d8e63c91687e1722c6454fb00b2a21",
    "legoin_mobilenetv3_part.pt": (
        "e8c596a91b85ada2985670e1289a862240f03536456b675e8f691158baaa5e5b"
    ),
    "legoin_mobilenetv3_part_labels.json": (
        "9ead94b90b3254886b79dc545602248e1c2ecc65ac3d65e31daea161601049fc"
    ),
    "legoin_warna.json": "bda4af6c3595570274dcb91379571573704c0c42d2b68d7de0a02c216b473634",
}


def test_v0_2_0_hashes_are_the_release_notes():
    assert MANIFESTS["v0.2.0"] == RELEASE_NOTES_V0_2_0


def fake_release(contents: dict[str, bytes]):
    """A fetch that serves made-up files, and the manifest that matches them."""
    calls = []

    def fetch(url: str, target: Path) -> None:
        calls.append(url)
        target.write_bytes(contents[url.rsplit("/", 1)[1]])

    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    return fetch, manifest, calls


def test_missing_files_are_downloaded_checked_and_cached(tmp_path, monkeypatch):
    fetch, manifest, calls = fake_release({"a.pt": b"weights", "b.json": b"{}"})
    monkeypatch.setitem(MANIFESTS, "v9.9.9", manifest)

    files = ensure_models("v9.9.9", tmp_path, fetch)
    again = ensure_models("v9.9.9", tmp_path, fetch)

    assert (
        files == again == {"a.pt": tmp_path / "v9.9.9/a.pt", "b.json": tmp_path / "v9.9.9/b.json"}
    )
    assert calls == [
        "https://github.com/al-dioooo/legoin-ml/releases/download/v9.9.9/a.pt",
        "https://github.com/al-dioooo/legoin-ml/releases/download/v9.9.9/b.json",
    ]


def test_a_file_that_isnt_the_released_one_is_refused(tmp_path, monkeypatch):
    fetch, manifest, _ = fake_release({"a.pt": b"weights"})
    monkeypatch.setitem(MANIFESTS, "v9.9.9", manifest | {"a.pt": "0" * 64})

    with pytest.raises(ModelFileError, match="SHA-256"):
        ensure_models("v9.9.9", tmp_path, fetch)
    assert not any((tmp_path / "v9.9.9").iterdir())


def test_a_changed_cached_file_is_downloaded_again(tmp_path, monkeypatch):
    fetch, manifest, calls = fake_release({"a.pt": b"weights"})
    monkeypatch.setitem(MANIFESTS, "v9.9.9", manifest)
    (tmp_path / "v9.9.9").mkdir()
    (tmp_path / "v9.9.9/a.pt").write_bytes(b"tampered")

    ensure_models("v9.9.9", tmp_path, fetch)

    assert (tmp_path / "v9.9.9/a.pt").read_bytes() == b"weights"
    assert len(calls) == 1


def test_a_failed_download_is_tried_again(tmp_path, monkeypatch):
    fetch, manifest, _ = fake_release({"a.pt": b"weights"})
    monkeypatch.setitem(MANIFESTS, "v9.9.9", manifest)
    failures = [TimeoutError("The read operation timed out")]

    def flaky(url, target):
        if failures:
            raise failures.pop()
        fetch(url, target)

    assert ensure_models("v9.9.9", tmp_path, flaky)["a.pt"].read_bytes() == b"weights"


def test_an_unknown_tag_is_refused(tmp_path):
    with pytest.raises(ModelFileError, match=r"v0\.0\.1"):
        ensure_models("v0.0.1", tmp_path)


def test_models_are_cached_in_the_data_directory_unless_told_otherwise(tmp_path):
    assert Settings(data_dir=tmp_path).models_path == tmp_path / "models"
    assert Settings(data_dir=tmp_path, model_dir="/opt/models").models_path == Path("/opt/models")


def label_map() -> dict:
    """legoin_mobilenetv3_part_labels.json from v0.2.0: the fields the backend checks."""
    return {
        "classes": list(PART_IDS),
        "design_ids": dict(DESIGN_IDS),
        "num_outputs": 14,
        "other_index": 13,
        "input_size": 224,
        "pad_color": [124, 116, 104],
        "mean": [0.485, 0.456, 0.406],
        "std": [0.229, 0.224, 0.225],
        "temperature": 0.7493,
    }


def test_the_v0_2_0_label_map_matches_the_release_notes():
    check_label_map(label_map())
    assert TEMPERATURE == 0.7493


@pytest.mark.parametrize(("key", "value"), [("temperature", 0.75), ("pad_color", [0, 0, 0])])
def test_a_label_map_with_other_settings_is_refused(key, value):
    with pytest.raises(ValueError, match=key):
        check_label_map(label_map() | {key: value})


def test_the_health_check_names_the_model_tag_when_models_run(settings):
    from legoin_api.routes.health import health

    class Request:
        class app:
            class state:
                settings = Settings(pipeline="models")

    assert health(Request)["modelTag"] == "v0.2.0"
    assert json.dumps(health(Request))
