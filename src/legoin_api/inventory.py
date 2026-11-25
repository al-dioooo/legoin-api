"""The inventory and corrections, ported from legoin-web's `src/lib/inventory.ts`.

The inventory isn't stored or sent: it's the detections grouped by part and color, biggest
quantity first, then by part id, then by color id. (The ids are lowercase ASCII, so Python's
string order matches the web app's `localeCompare`.)
"""

from collections.abc import Callable, Iterable

from legoin_api.schemas import (
    AddChange,
    Detection,
    DetectionChange,
    EditChange,
    IdeaSet,
    PartCount,
    RemoveChange,
)


def merge(lines: Iterable[PartCount]) -> list[PartCount]:
    """Adds up lines with the same part and color, biggest groups first."""
    totals: dict[tuple[str, str], int] = {}
    for line in lines:
        key = (line.part, line.color)
        totals[key] = totals.get(key, 0) + line.quantity
    merged = [
        PartCount(part=part, color=color, quantity=quantity)
        for (part, color), quantity in totals.items()
        if quantity > 0
    ]
    return sorted(merged, key=lambda line: (-line.quantity, line.part, line.color))


def to_inventory(detections: Iterable[Detection]) -> list[PartCount]:
    return merge(PartCount(part=d.part, color=d.color, quantity=1) for d in detections)


def same_inventory(a: Iterable[Detection], b: Iterable[Detection]) -> bool:
    """The same parts in the same colors and counts. Boxes, ids and `edited` don't count."""
    return to_inventory(a) == to_inventory(b)


def apply_changes(
    detections: list[Detection],
    changes: Iterable[DetectionChange],
    new_id: Callable[[], str],
) -> list[Detection]:
    """Applies corrections in order. Unknown ids are ignored; added pieces have no box."""
    result = list(detections)
    for change in changes:
        if isinstance(change, RemoveChange):
            result = [d for d in result if d.id != change.id]
        elif isinstance(change, EditChange):
            result = [
                d.model_copy(
                    update={
                        "part": change.part or d.part,
                        "color": change.color or d.color,
                        "edited": True,
                    }
                )
                if d.id == change.id
                else d
                for d in result
            ]
        elif isinstance(change, AddChange):
            result += [
                Detection(
                    id=new_id(),
                    part=change.part,
                    color=change.color,
                    box=None,
                    confidence=None,
                    edited=True,
                )
                for _ in range(change.quantity)
            ]
        else:
            restored = change.detection.model_copy(
                update={"edited": True if change.detection.edited else None}
            )
            if any(d.id == restored.id for d in result):
                result = [restored if d.id == restored.id else d for d in result]
            else:
                result.append(restored)
    return result


def cek_inventori(
    required: Iterable[PartCount], inventory: Iterable[PartCount]
) -> tuple[list[PartCount], list[PartCount]]:
    """The PRD's `cek_inventori`: splits what a build needs into `used` and `missing`.

    A port of legoin-web's `checkInventory`. For each line, `used` gets what the inventory
    covers, up to what's needed, and `missing` gets the rest, so a part the person doesn't
    have is never counted as owned. `used` plus `missing` is exactly what the build needs.
    """
    available = {(line.part, line.color): line.quantity for line in merge(inventory)}
    used: list[PartCount] = []
    missing: list[PartCount] = []
    for line in merge(required):
        owned = min(line.quantity, available.get((line.part, line.color), 0))
        if owned > 0:
            used.append(line.model_copy(update={"quantity": owned}))
        if line.quantity > owned:
            missing.append(line.model_copy(update={"quantity": line.quantity - owned}))
    return used, missing


def recheck(ideas: IdeaSet, inventory: list[PartCount]) -> IdeaSet:
    """The same builds, with `used` and `missing` worked out again against `inventory`."""
    items = []
    for idea in ideas.items:
        used, missing = cek_inventori(idea.used + idea.missing, inventory)
        items.append(idea.model_copy(update={"used": used, "missing": missing}))
    return ideas.model_copy(update={"items": items})
