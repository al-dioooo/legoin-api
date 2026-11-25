import pytest
from pydantic import ValidationError

from legoin_api.config import Settings


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
