from images import pieces

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
    job = jobs.submit("device", pieces())
    jobs._executor.submit(lambda: None).result(timeout=5)
    assert jobs.get("device", job.id).status == "done"

    now[0] = 599.0
    assert jobs.get("device", job.id) is not None
    now[0] = 601.0
    assert jobs.get("device", job.id) is None
    jobs.shutdown()
