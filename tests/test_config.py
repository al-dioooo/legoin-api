import pytest
from pydantic import ValidationError

from legoin_api.config import Settings
from legoin_api.main import warn_about_deployment


def test_the_models_pipeline_refuses_more_than_one_scan_worker(monkeypatch):
    monkeypatch.setenv("LEGOIN_PIPELINE", "models")
    monkeypatch.setenv("LEGOIN_SCAN_WORKERS", "2")

    with pytest.raises(ValidationError, match="LEGOIN_SCAN_WORKERS must be 1"):
        Settings()


def test_the_stub_may_run_several_scan_workers():
    assert Settings(pipeline="stub", scan_workers=4).scan_workers == 4
    assert Settings(pipeline="models", scan_workers=1).scan_workers == 1


@pytest.mark.parametrize("field", ["scan_workers", "scan_queue_size"])
def test_scan_workers_and_queue_size_are_at_least_one(field):
    with pytest.raises(ValidationError):
        Settings(**{field: 0})


@pytest.mark.parametrize(
    ("container", "public_url", "warns"),
    [(True, None, True), (True, "https://api.example.com", False), (False, None, False)],
)
def test_the_container_warns_when_the_public_url_is_unset(caplog, container, public_url, warns):
    warn_about_deployment(Settings(container=container, public_url=public_url))

    assert ("LEGOIN_PUBLIC_URL is unset" in caplog.text) is warns


def test_the_image_says_it_is_a_container():
    from pathlib import Path

    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()
    assert "LEGOIN_CONTAINER=1" in dockerfile
