"""The contract's shapes (section 3), as pydantic models.

Keys are camelCase on the wire, with the same names as legoin-web's `domain.ts`. Optional
fields are left out when they have no value, never sent as `null`; a detection's `box` and
`confidence` are the exception, always present and `null` for a piece added by hand.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

from legoin_api.catalogue import ColorId, PartId

Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
"""Opaque, URL-safe, at most 64 characters. The backend makes them; clients never parse them."""

Fraction = Annotated[float, Field(ge=0, le=1)]

ScanStage = Literal["detecting", "classifying", "coloring"]
Theme = Literal["vehicles", "animals", "buildings", "surprise"]
IdeaTheme = Literal["vehicles", "animals", "buildings"]
Difficulty = Literal["easy", "medium", "hard"]
Locale = Literal["en", "id"]

ErrorCode = Literal[
    "invalid_request",
    "not_found",
    "no_pieces",
    "photo_too_large",
    "photo_type",
    "too_dark",
    "too_blurry",
    "timeout",
    "unavailable",
    "internal",
    "unauthorized",
    "invalid_credentials",
    "email_taken",
    "rate_limited",
    # Picture builds (contract section 13, ADR-0012).
    "picture_limit",
    "picture_budget",
    "picture_unusable",
    "picture_refused",
    "no_valid_build",
]

# Leaves an optional field out of the answer instead of sending `null`.
_omit_none = {"default": None, "exclude_if": lambda value: value is None}


class WireModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    def wire(self) -> dict:
        """The JSON body, as the contract writes it."""
        return self.model_dump(mode="json", by_alias=True)


class Box(WireModel):
    """Fractions of the photo, 0 to 1, from its top-left corner."""

    x: Fraction
    y: Fraction
    width: Fraction
    height: Fraction

    @model_validator(mode="after")
    def _inside_the_photo(self) -> "Box":
        if self.x + self.width > 1 + 1e-9 or self.y + self.height > 1 + 1e-9:
            raise ValueError("x + width and y + height must be at most 1")
        return self


class Photo(WireModel):
    src: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class Detection(WireModel):
    id: Id
    part: PartId
    color: ColorId
    box: Box | None
    confidence: Fraction | None
    # `false` means the same as left out, so only `true` is ever sent.
    edited: bool | None = Field(default=None, exclude_if=lambda value: not value)


class PartCount(WireModel):
    part: PartId
    color: ColorId
    quantity: int = Field(ge=1)


class Preview(WireModel):
    """A checked build's picture and LDraw model, as absolute URLs (ADR-0010)."""

    image: str
    model: str


class Idea(WireModel):
    id: Id
    theme: IdeaTheme
    difficulty: Difficulty
    title: str
    summary: str
    steps: list[str]
    used: list[PartCount]
    missing: list[PartCount]
    build_id: Id | None = Field(**_omit_none)
    """The library build this idea is. Kept with the idea; its `preview` is added on the way
    out, so its URLs follow the public URL and the files' versions."""
    preview: Preview | None = Field(**_omit_none)


class IdeaSet(WireModel):
    theme: Theme
    locale: Locale
    items: list[Idea]


class StoredIdea(WireModel):
    idea: Idea
    set: IdeaSet


class Scan(WireModel):
    id: Id
    created_at: str
    photo: Photo
    detections: list[Detection]
    last_theme: Theme | None = Field(**_omit_none)
    ideas: IdeaSet | None = Field(**_omit_none)


class ScanList(WireModel):
    scans: list[Scan]


class Account(WireModel):
    id: Id
    email: str
    created_at: str


class Session(WireModel):
    """Only on the wire: what registering or logging in answers."""

    token: str
    expires_at: str
    account: Account


Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_lower=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    ),
]
"""Trimmed and lowercased, so one address is one account however it's typed."""

DeviceId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,128}$")]
"""The `Legoin-Device-Id` a browser sent before accounts, as the header allowed it."""


class Registration(WireModel):
    email: Email
    password: Annotated[str, StringConstraints(min_length=8, max_length=128)]
    device_id: DeviceId | None = None


class LogIn(WireModel):
    """Only presence and length: a password made under older rules still logs in."""

    email: Annotated[
        str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=254)
    ]
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    device_id: DeviceId | None = None


class ErrorDetail(WireModel):
    code: ErrorCode
    message: str
    field: str | None = Field(**_omit_none)


class ApiErrorBody(WireModel):
    error: ErrorDetail


class RunningJob(WireModel):
    id: Id
    status: Literal["running"] = "running"
    stage: ScanStage


class DoneJob(WireModel):
    id: Id
    status: Literal["done"] = "done"
    scan: Scan


class FailedJob(WireModel):
    id: Id
    status: Literal["failed"] = "failed"
    error: ErrorDetail


ScanJob = Annotated[RunningJob | DoneJob | FailedJob, Field(discriminator="status")]


# Requests


class EditChange(WireModel):
    type: Literal["edit"]
    id: Id
    part: PartId | None = None
    color: ColorId | None = None


class RemoveChange(WireModel):
    type: Literal["remove"]
    id: Id


class AddChange(WireModel):
    type: Literal["add"]
    part: PartId
    color: ColorId
    quantity: int = Field(ge=1, le=20)


class RestoreChange(WireModel):
    type: Literal["restore"]
    detection: Detection


DetectionChange = Annotated[
    EditChange | RemoveChange | AddChange | RestoreChange, Field(discriminator="type")
]

CHANGE_TYPES = frozenset({"edit", "remove", "add", "restore"})


class DetectionChanges(WireModel):
    changes: list[DetectionChange] = Field(min_length=1)


class IdeasRequest(WireModel):
    theme: Theme
    locale: Locale
    exclude: list[Id] = Field(default_factory=list, max_length=50)
