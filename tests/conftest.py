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
