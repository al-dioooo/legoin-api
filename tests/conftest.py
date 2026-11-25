import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from legoin_api.config import Settings
from legoin_api.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(pipeline="stub", data_dir=tmp_path / "data")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


CONTRACT_DIR = Path(__file__).parent / "contract"


@pytest.fixture
def example():
    """Loads one of the contract's example bodies from `tests/contract/`."""

    def load(name: str) -> dict:
        return json.loads((CONTRACT_DIR / f"{name}.json").read_text())

    return load


DEVICE = "3b0e6f1c-5d2a-4f7e-9c41-8a2d7e5b9f10"
"""The device id in the contract's examples."""


@pytest.fixture
def seed(client):
    """Puts a scan straight into the store, with a small photo file, and returns it."""
    from legoin_api.schemas import Detection, IdeaSet
    from legoin_api.store import StoredScan

    def put(scan: dict, device: str = DEVICE, **extra) -> StoredScan:
        photos = client.app.state.photos
        name = photos.new_name()
        (photos.directory / name).write_bytes(b"\xff\xd8\xff\xd9")
        stored = StoredScan(
            id=scan["id"],
            device_id=device,
            created_at=scan["createdAt"],
            photo_file=name,
            photo_width=scan["photo"]["width"],
            photo_height=scan["photo"]["height"],
            detections=[Detection.model_validate(d) for d in scan["detections"]],
            last_theme=extra.get("last_theme", scan.get("lastTheme")),
            ideas=IdeaSet.model_validate(extra["ideas"]) if extra.get("ideas") else None,
        )
        client.app.state.store.insert_scan(stored)
        return stored

    return put
