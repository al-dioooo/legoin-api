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
