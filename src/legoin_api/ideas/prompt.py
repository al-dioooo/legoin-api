"""What Claude is told when it writes ideas, and the shape it answers in (ADR-0009).

The system prompt never changes between requests, so it's cached: the catalogue with ids and
names in both languages, the rules, the library of checked builds (ADR-0010) and one example.
Everything that changes per request, the inventory, themes, language and ideas already seen,
comes after it in the user message.
"""

import json
from collections.abc import Iterable

from legoin_api.builds import LibraryBuild
from legoin_api.catalogue import COLOR_IDS, DESIGN_IDS, PART_IDS, ColorId, PartId
from legoin_api.ideas.generator import IdeaRequest
from legoin_api.schemas import Locale

PART_NAMES: dict[PartId, str] = {
    "brick-1x1": "Brick 1×1",
    "brick-1x2": "Brick 1×2",
    "brick-1x3": "Brick 1×3",
    "brick-1x4": "Brick 1×4",
    "brick-2x2": "Brick 2×2",
    "brick-2x3": "Brick 2×3",
    "brick-2x4": "Brick 2×4",
    "plate-1x2": "Plate 1×2",
    "plate-2x2": "Plate 2×2",
    "plate-2x4": "Plate 2×4",
    "slope-2x2": "Slope 45° 2×2",
    "tile-1x2": "Tile 1×2",
    "round-1x1": "Round 1×1",
}
"""legoin-web's labels (`src/lib/bricks.ts`): the hobby's English terms, in both languages."""

COLOR_NAMES: dict[ColorId, dict[Locale, str]] = {
    "red": {"en": "Red", "id": "Merah"},
    "blue": {"en": "Blue", "id": "Biru"},
    "yellow": {"en": "Yellow", "id": "Kuning"},
    "green": {"en": "Green", "id": "Hijau"},
    "black": {"en": "Black", "id": "Hitam"},
    "white": {"en": "White", "id": "Putih"},
    "light-gray": {"en": "Light gray", "id": "Abu-abu terang"},
    "dark-gray": {"en": "Dark gray", "id": "Abu-abu gelap"},
    "orange": {"en": "Orange", "id": "Oranye"},
    "brown": {"en": "Brown", "id": "Cokelat"},
}
"""legoin-web's labels (`src/lib/bricks.ts`) in English and Indonesian."""

LANGUAGES: dict[Locale, str] = {"en": "English (en)", "id": "Indonesian (id)"}

_PART_LINE = {
    "type": "object",
    "properties": {
        "part": {"type": "string", "enum": list(PART_IDS)},
        "color": {"type": "string", "enum": list(COLOR_IDS)},
        "quantity": {"type": "integer"},
    },
    "required": ["part", "color", "quantity"],
    "additionalProperties": False,
}


def ideas_schema(build_ids: Iterable[str] = ()) -> dict:
    """The answer's shape, for structured outputs. `buildId` is a library build's id, or ""
    for a build Claude writes itself. Numeric and length limits aren't allowed there, so the
    generator checks quantities and counts itself."""
    return {
        "type": "object",
        "properties": {
            "ideas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "buildId": {"type": "string", "enum": ["", *build_ids]},
                        "theme": {"type": "string", "enum": ["vehicles", "animals", "buildings"]},
                        "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "steps": {"type": "array", "items": {"type": "string"}},
                        "parts": {"type": "array", "items": _PART_LINE},
                    },
                    "required": [
                        "buildId",
                        "theme",
                        "difficulty",
                        "title",
                        "summary",
                        "steps",
                        "parts",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["ideas"],
        "additionalProperties": False,
    }


_EXAMPLE_ANSWER = {
    "ideas": [
        {
            "buildId": "",
            "theme": "animals",
            "difficulty": "easy",
            "title": "Bebek kuning",
            "summary": "Badan kuning, paruh oranye, mata hitam.",
            "steps": [
                "Tumpuk dua brick 2×2 kuning sebagai badan dan kepala.",
                "Pasang plate 1×2 oranye di depan kepala sebagai paruh.",
                "Tambahkan dua round 1×1 hitam di atas kepala sebagai mata.",
            ],
            "parts": [
                {"part": "brick-2x2", "color": "yellow", "quantity": 2},
                {"part": "plate-1x2", "color": "orange", "quantity": 1},
                {"part": "round-1x1", "color": "black", "quantity": 2},
            ],
        }
    ]
}


def _catalogue() -> str:
    parts = "\n".join(
        f"- {part}: {PART_NAMES[part]} (LEGO {DESIGN_IDS[part]})" for part in PART_IDS
    )
    colors = "\n".join(
        f"- {color}: {COLOR_NAMES[color]['en']} / {COLOR_NAMES[color]['id']}" for color in COLOR_IDS
    )
    return (
        "Parts, as id: name. Part names are the hobby's English terms in both languages.\n"
        f"{parts}\n\n"
        "Colors, as id: English / Indonesian name.\n"
        f"{colors}"
    )


def _library(builds: Iterable[LibraryBuild]) -> str:
    lines = "\n".join(
        f"- {build.id} ({build.theme}): {build.title['en']} / {build.title['id']}: "
        + ", ".join(f"{line.part} {line.color} ×{line.quantity}" for line in build.parts)
        for build in builds
    )
    if not lines:
        return ""
    return f"""
# The library

These builds have been checked and have a picture the app shows. Each line is a build's id, \
its theme, its title in English / Indonesian, and every piece it needs.

{lines}

Choose from the library by comparing these pieces with the person's; that needs no tool \
call. When the person owns every piece of a library build in a slot's theme, or all but \
one, use it for that slot, so they see its picture: set `buildId` to its id and `parts` to \
its pieces exactly, and leave `title`, `summary` and `steps` empty, since the app shows the \
build's own. Use each library build at most once, and not one the person has already \
seen. For every other build, set `buildId` to "" and write it yourself.
"""


def system_prompt(builds: Iterable[LibraryBuild] = ()) -> str:
    """The stable, cached part of the prompt, with the library's builds in a fixed order."""
    return f"""\
You write small LEGO build ideas for Legoin, an app where someone photographs their loose \
LEGO pieces and asks what they could build with them. Each request gives the pieces they \
have, the theme of each build wanted, and the language to write in.

# The catalogue

These are the only parts and colors that exist. Use these ids exactly.

{_catalogue()}

# How to write the builds

- Write exactly the builds asked for, one per slot, in the slots' order, each with its \
slot's theme: vehicles, animals or buildings.
- Prefer builds made mostly from the person's own pieces. Use what they have first; a build \
may need a few pieces they don't have, but keep those few.
- Keep each build small and buildable by hand: about 3 to 15 pieces and 2 to 5 short steps. \
Set its difficulty to easy, medium or hard.
- List every piece the build needs in `parts`, owned or not, as a catalogue part id, color \
id and quantity of 1 or more. Each part and color pair appears once.
- The title is a few words. The summary is one short sentence about how it looks. Each step \
is one short instruction that names the pieces it uses.
- Write the title, summary and steps in the requested language. In Indonesian, write \
natural, friendly Indonesian, as a person would say it. Color names are Indonesian and \
part names stay in the hobby's English, color after the part: "brick 2×4 merah", \
"plate 1×2 biru". In English, write "red 2×4 brick" or "blue 1×2 plate".
- Each build is different from the others in the answer and from every idea the person \
has already seen.
{_library(builds)}
# Checking the pieces

The `cek_inventori` tool says which of a build's pieces the person owns and which are \
missing. Before answering, call it once for each build, library builds included, with the \
build's full parts list, all in the same turn. You get that one turn of tool calls. If a \
build is missing pieces that something they own could replace, change it, then answer.

# Example

Pieces the person has:
- brick-2x2 yellow ×2
- plate-1x2 orange ×1
- round-1x1 black ×2
- brick-1x2 white ×1

Builds wanted, in order:
1. animals

Language: Indonesian (id)

Answer:
{json.dumps(_EXAMPLE_ANSWER, ensure_ascii=False)}
"""


IDEAS_SCHEMA = ideas_schema()
SYSTEM_PROMPT = system_prompt()
"""Without a library: Claude writes every build itself."""


def user_prompt(request: IdeaRequest) -> str:
    """The part of the prompt that changes per request; it follows the cached system prompt."""
    pieces = "\n".join(f"- {line.part} {line.color} ×{line.quantity}" for line in request.inventory)
    slots = "\n".join(f"{n}. {theme}" for n, theme in enumerate(request.themes, start=1))
    text = (
        f"Pieces the person has:\n{pieces}\n\n"
        f"Builds wanted, in order:\n{slots}\n\n"
        f"Language: {LANGUAGES[request.locale]}"
    )
    if request.excluded:
        seen = "\n".join(f"- {idea.title}" for idea in request.excluded)
        text += f"\n\nIdeas the person has already seen, so write different ones:\n{seen}"
    return text


def repair_prompt(problems: dict[int, list[str]]) -> str:
    """Asks once for replacements of the builds that broke the rules, by slot number."""
    lines = "\n".join(
        f"- Build {slot + 1}: {'; '.join(found)}" for slot, found in sorted(problems.items())
    )
    return (
        "Some builds can't be used:\n"
        f"{lines}\n\n"
        "Answer again with every build, in the same order. Keep the good ones as they are and "
        "write new ones for these, using only the catalogue."
    )
