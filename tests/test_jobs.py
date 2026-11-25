import threading

from conftest import ACCOUNT
from images import pieces
from test_scan_jobs import GatedPipeline

from legoin_api.jobs import JobManager, floor4, to_box
from legoin_api.photos import PhotoStore
from legoin_api.pipeline import Found
from legoin_api.pipeline.stub import StubPipeline
from legoin_api.store import Store


def test_boxes_become_fractions_inside_the_photo():
    assert to_box(Found(60, 40, 300, 240, 0.9), 1200, 800).wire() == {
        "x": 0.05,
        "y": 0.05,
        "width": 0.2,
        "height": 0.25,
    }
    # A box that spills past the edge is clipped to it.
    clipped = to_box(Found(-5, 700, 1300, 820, 0.9), 1200, 800)
    assert (clipped.x, clipped.width, clipped.y + clipped.height) == (0.0, 1.0, 1.0)


def test_confidence_rounds_down():
    assert floor4(0.599999) == 0.5999
    assert floor4(0.94) == 0.94


def test_ended_jobs_are_forgotten_after_ten_minutes(tmp_path):
    now = [0.0]
    jobs = JobManager(
        StubPipeline(),
        Store(tmp_path / "db"),
        PhotoStore(tmp_path / "photos"),
        clock=lambda: now[0],
    )
    job = jobs.submit(ACCOUNT, pieces())
    jobs._executor.submit(lambda: None).result(timeout=5)
    assert jobs.get(ACCOUNT, job.id).status == "done"

    now[0] = 599.0
    assert jobs.get(ACCOUNT, job.id) is not None
    now[0] = 601.0
    assert jobs.get(ACCOUNT, job.id) is None
    jobs.shutdown()


def test_shutting_down_waits_for_the_running_job_and_saves_nothing(tmp_path):
    pipeline = GatedPipeline()
    store, photos = Store(tmp_path / "db"), PhotoStore(tmp_path / "photos")
    jobs = JobManager(pipeline, store, photos)
    jobs.submit(ACCOUNT, pieces())
    jobs.submit(ACCOUNT, pieces())
    assert pipeline.entered["detect"].wait(5)

    stopping = threading.Thread(target=jobs.shutdown)
    stopping.start()
    stopping.join(0.2)
    assert stopping.is_alive()

    pipeline.open_all()
    stopping.join(2)
    assert not stopping.is_alive()
    # The running job stopped at its next stage and the waiting one never started.
    assert not pipeline.entered["classify"].is_set()
    assert store.list_scans(ACCOUNT) == []
    assert not any(photos.directory.iterdir())
    store.close()


def test_shutting_down_gives_up_on_a_stuck_job(tmp_path, caplog):
    pipeline = GatedPipeline()
    jobs = JobManager(pipeline, Store(tmp_path / "db"), PhotoStore(tmp_path / "photos"))
    jobs.submit(ACCOUNT, pieces())
    assert pipeline.entered["detect"].wait(5)

    jobs.shutdown(timeout_s=0.1)

    assert "still running" in caplog.text
    pipeline.open_all()
