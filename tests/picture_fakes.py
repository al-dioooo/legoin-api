"""Fakes for builds from scans: Claude, BrickLayer's checks and the renderer."""

import io
from types import SimpleNamespace

from PIL import Image

from legoin_api.picture.prompt import BuildBrief
from legoin_api.schemas import PartCount

CAR = [
    {"part": "plate-2x4", "color": "black", "x": 0, "y": 0, "z": 0, "rotation": 90},
    {"part": "brick-2x4", "color": "red", "x": 0, "y": 1, "z": 1, "rotation": 90},
    {"part": "slope-2x2", "color": "green", "x": 0, "y": 1, "z": 4, "rotation": 0},
    {"part": "brick-2x2", "color": "black", "x": 0, "y": 3, "z": 4, "rotation": 0},
    {"part": "round-1x1", "color": "yellow", "x": 0, "y": 0, "z": 1, "rotation": 0},
    {"part": "round-1x1", "color": "yellow", "x": 1, "y": 0, "z": 1, "rotation": 0},
]
"""The race car from the prompt's example: valid in BrickLayer."""

PIECES = [
    PartCount(part="plate-2x4", color="black", quantity=1),
    PartCount(part="brick-2x4", color="red", quantity=1),
    PartCount(part="slope-2x2", color="green", quantity=1),
    PartCount(part="brick-2x2", color="black", quantity=1),
    PartCount(part="round-1x1", color="yellow", quantity=2),
    PartCount(part="brick-1x1", color="white", quantity=2),
]
"""A scan's pieces: the car's, and two white 1×1 bricks to spare."""

BRIEF = BuildBrief(theme="vehicles", kind="a vehicle for the road", max_pieces=40)

FINISH = {
    "title": "Little race car",
    "summary": "A red car with a green hood.",
    "steps": ["Lay the black 2×4 plate.", "Add the body, hood, cab and lights."],
}


def tool(name: str, input: dict | None = None, id: str | None = None):
    return SimpleNamespace(type="tool_use", id=id or f"toolu_{name}", name=name, input=input or {})


def text(value: str):
    return SimpleNamespace(type="text", text=value)


def usage(input=10, output=500, read=4_000, write=1_000):
    return SimpleNamespace(
        input_tokens=input,
        output_tokens=output,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=write,
    )


class FakeClaude:
    """Answers each call with the next turn of a script. A turn is a list of blocks, or a
    `(blocks, stop_reason)` pair; tool use is the stop reason when there are tool calls."""

    def __init__(self, turns, *, model="claude-opus-5-5", spend=None):
        self.turns = list(turns)
        self.model = model
        self.spend = spend or usage()
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        # The messages list grows after the call, so keep what was sent.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        if not self.turns:
            raise AssertionError("Claude was called more often than the script has turns")
        turn = self.turns.pop(0)
        blocks, stop = turn if isinstance(turn, tuple) else (turn, None)
        if stop is None:
            stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        return SimpleNamespace(
            content=blocks,
            stop_reason=stop,
            stop_details=None,
            usage=self.spend,
            model=self.model,
        )


class FakeChecks:
    """Valid unless a piece id is in `bad`, with at least one piece."""

    def __init__(self, bad=()):
        self.bad = set(bad)
        self.calls = 0

    def problems(self, pieces):
        self.calls += 1
        wrong = sorted(p.id for p in pieces if p.id in self.bad)
        return [f"1 floating: {', '.join(wrong)}"] if wrong else []


class FakeRenders:
    def __init__(self):
        self.views_asked: list[list[str]] = []
        self.previews = 0

    def views(self, pieces, views):
        self.views_asked.append(list(views))
        return png()

    def preview(self, pieces, title):
        self.previews += 1
        return b"RIFF-fake-webp"


def png(size=(8, 8)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "white").save(out, "PNG")
    return out.getvalue()


def jpeg(size=(1200, 800), exif: bool = False) -> bytes:
    image = Image.new("RGB", size, (200, 30, 40))
    out = io.BytesIO()
    if exif:
        data = Image.Exif()
        data[0x010F] = "Camera maker"  # Make
        image.save(out, "JPEG", exif=data)
    else:
        image.save(out, "JPEG")
    return out.getvalue()


def happy_script(finish=FINISH):
    """Place the car, validate and render it, then finish in the next turn."""
    return [
        [
            tool("place_pieces", {"pieces": CAR}, "t1"),
            tool("validate", id="t2"),
            tool("render", {"view": "all"}, "t3"),
        ],
        [tool("finish", finish, "t4")],
    ]
