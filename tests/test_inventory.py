from legoin_api.inventory import apply_changes, same_inventory, to_inventory
from legoin_api.schemas import AddChange, Detection, EditChange, RemoveChange, RestoreChange


def piece(id: str, part: str, color: str, **extra) -> Detection:
    fields = {"box": None, "confidence": None} | extra
    return Detection(id=id, part=part, color=color, **fields)


def ids():
    count = 0

    def next_id() -> str:
        nonlocal count
        count += 1
        return f"new_{count}"

    return next_id


def test_the_inventory_groups_by_part_and_color_biggest_first():
    detections = [
        piece("a", "slope-2x2", "green"),
        piece("b", "brick-2x4", "red"),
        piece("c", "brick-2x2", "black"),
        piece("d", "brick-2x4", "red"),
        piece("e", "brick-2x2", "blue"),
    ]

    assert [line.wire() for line in to_inventory(detections)] == [
        {"part": "brick-2x4", "color": "red", "quantity": 2},
        {"part": "brick-2x2", "color": "black", "quantity": 1},
        {"part": "brick-2x2", "color": "blue", "quantity": 1},
        {"part": "slope-2x2", "color": "green", "quantity": 1},
    ]


def test_same_inventory_ignores_ids_boxes_and_edited():
    before = [piece("a", "brick-1x1", "red", confidence=0.4, box=None)]
    after = [piece("z", "brick-1x1", "red", edited=True)]

    assert same_inventory(before, after)
    assert not same_inventory(before, [piece("a", "brick-1x1", "blue")])


def test_changes_apply_in_order():
    detections = [piece("a", "brick-1x1", "red"), piece("b", "brick-1x2", "blue")]
    changes = [
        EditChange(type="edit", id="a", color="yellow"),
        RemoveChange(type="remove", id="b"),
        AddChange(type="add", part="tile-1x2", color="white", quantity=2),
        EditChange(type="edit", id="new_2", part="plate-1x2"),
    ]

    result = apply_changes(detections, changes, ids())

    assert [(d.id, d.part, d.color, d.edited) for d in result] == [
        ("a", "brick-1x1", "yellow", True),
        ("new_1", "tile-1x2", "white", True),
        ("new_2", "plate-1x2", "white", True),
    ]
    assert result[1].box is None and result[1].confidence is None


def test_an_edit_with_neither_part_nor_color_confirms_the_piece():
    result = apply_changes(
        [piece("a", "brick-1x1", "red", confidence=0.5)], [EditChange(type="edit", id="a")], ids()
    )

    assert (result[0].part, result[0].color, result[0].confidence, result[0].edited) == (
        "brick-1x1",
        "red",
        0.5,
        True,
    )


def test_unknown_ids_change_nothing():
    detections = [piece("a", "brick-1x1", "red")]
    changes = [RemoveChange(type="remove", id="zz"), EditChange(type="edit", id="zz", color="blue")]

    assert apply_changes(detections, changes, ids()) == detections


def test_restore_replaces_a_piece_or_adds_it_back():
    original = piece("a", "brick-1x1", "red", confidence=0.5)
    edited = original.model_copy(update={"color": "blue", "edited": True})
    removed = piece("b", "brick-1x2", "green", confidence=0.9)

    result = apply_changes(
        [edited],
        [
            RestoreChange(type="restore", detection=original),
            RestoreChange(type="restore", detection=removed),
        ],
        ids(),
    )

    assert result == [original, removed]
    assert "edited" not in result[0].wire()
