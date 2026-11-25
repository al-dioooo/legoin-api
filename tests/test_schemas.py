import pytest
from pydantic import TypeAdapter, ValidationError

from legoin_api.catalogue import COLOR_IDS, DESIGN_IDS, PART_IDS, REBRICKABLE_COLOR_IDS
from legoin_api.schemas import (
    ApiErrorBody,
    Detection,
    DetectionChanges,
    IdeaSet,
    IdeasRequest,
    Scan,
    ScanJob,
    ScanList,
    StoredIdea,
)

job = TypeAdapter(ScanJob)


@pytest.mark.parametrize(
    "name",
    ["scan-job-accepted", "scan-job-running", "scan-job-done", "scan-job-failed"],
)
def test_scan_jobs_round_trip_the_contract_examples(example, name):
    body = example(name)

    assert job.dump_python(job.validate_python(body), mode="json", by_alias=True) == body


@pytest.mark.parametrize(
    ("model", "name"),
    [
        (ScanList, "scans-list"),
        (Scan, "patch-detections-response"),
        (IdeaSet, "ideas-response"),
        (StoredIdea, "stored-idea"),
        (ApiErrorBody, "error-not-found"),
        (ApiErrorBody, "error-invalid-request"),
    ],
)
def test_answers_round_trip_the_contract_examples(example, model, name):
    body = example(name)

    assert model.model_validate(body).wire() == body


def test_requests_parse_the_contract_examples(example):
    changes = DetectionChanges.model_validate(example("patch-detections-request")).changes
    ideas = IdeasRequest.model_validate(example("ideas-request"))

    assert [change.type for change in changes] == ["edit", "add"]
    assert ideas.locale == "id"
    assert ideas.exclude == ["idea_m2x9", "idea_p0d4", "idea_t6w1"]


def test_a_piece_added_by_hand_keeps_null_box_and_confidence():
    piece = Detection(id="det_1", part="plate-1x2", color="blue", box=None, confidence=None)

    assert piece.wire() == {
        "id": "det_1",
        "part": "plate-1x2",
        "color": "blue",
        "box": None,
        "confidence": None,
    }


def test_edited_is_only_sent_when_true():
    fields = {"id": "det_1", "part": "brick-1x1", "color": "red", "box": None, "confidence": None}

    assert "edited" not in Detection(**fields, edited=False).wire()
    assert Detection(**fields, edited=True).wire()["edited"] is True


def test_a_scan_leaves_out_last_theme_and_ideas_until_there_are_some(example):
    scan = example("scan-job-done")["scan"]

    assert set(Scan.model_validate(scan).wire()) == {"id", "createdAt", "photo", "detections"}


@pytest.mark.parametrize(
    "box",
    [
        {"x": 0.8, "y": 0.1, "width": 0.3, "height": 0.1},
        {"x": 0.1, "y": -0.1, "width": 0.3, "height": 0.1},
        {"x": 0.1, "y": 0.1, "width": 0.3, "height": 1.2},
    ],
)
def test_a_box_stays_inside_the_photo(box):
    fields = {"id": "det_1", "part": "brick-1x1", "color": "red", "confidence": 0.9}

    with pytest.raises(ValidationError):
        Detection(**fields, box=box)


@pytest.mark.parametrize("bad_id", ["", "has space", "a/b", "x" * 65])
def test_ids_are_url_safe_and_short(bad_id):
    with pytest.raises(ValidationError):
        DetectionChanges.model_validate({"changes": [{"type": "remove", "id": bad_id}]})


def test_the_catalogue_is_the_contracts():
    assert len(PART_IDS) == 13
    assert len(COLOR_IDS) == 10
    assert set(DESIGN_IDS) == set(PART_IDS)
    assert set(REBRICKABLE_COLOR_IDS) == set(COLOR_IDS)
    assert DESIGN_IDS["tile-1x2"] == "3069b"
    assert REBRICKABLE_COLOR_IDS["light-gray"] == 71
