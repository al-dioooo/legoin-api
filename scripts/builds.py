# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "bricklayer-sdk==0.1.0",
#     "brickwright==0.1.0",
# ]
# ///
"""Checks, exports and renders the library of builds in `builds/`, and rewrites its manifest.

Authoring only (ADR-0010). The service reads `builds/manifest.json` and serves the files
this writes; it never runs BrickLayer or needs the LDraw parts library.

For each `builds/<id>/build.py`, a BrickWright build file with a `META` dict:

1. runs BrickWright's own validation (`brickwright external validate`): no collisions,
   nothing floating, and the file's `run_tests()`, which checks it's one connected assembly;
2. checks it's 3 to 15 pieces, all catalogue parts and colors, and that `META` is complete;
3. writes `model.ldr`, and renders `preview.webp`: an isometric view, 640 px square, on a
   transparent background;
4. lists its parts, counted from the build itself, in the manifest.

Run it with uv, which installs the pinned BrickLayer and BrickWright for this script only:

    LDRAW_LIBRARY_PATH=~/ldraw uv run --script scripts/builds.py           # changed builds
    LDRAW_LIBRARY_PATH=~/ldraw uv run --script scripts/builds.py race-car  # and this one
    LDRAW_LIBRARY_PATH=~/ldraw uv run --script scripts/builds.py --force   # every build
    uv run --script scripts/builds.py --check   # validate and compare; writes nothing

Every run validates every build and rewrites the manifest. A build is exported and rendered
again when its source or files no longer match the manifest, when it's named, or with
`--force`, which a change to the renderer needs.

Rendering needs the LDraw parts library (https://library.ldraw.org/library/updates/complete.zip),
unzipped outside the repository. It runs on a CPU: with no GPU, wgpu falls back to llvmpipe,
which lacks the `float32-filterable` feature pygfx asks for by default, so it's turned off.
"""

import argparse
import hashlib
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILDS = ROOT / "builds"
MANIFEST = BUILDS / "manifest.json"
sys.path.insert(0, str(ROOT / "src"))

from legoin_api.catalogue import DESIGN_IDS, REBRICKABLE_COLOR_IDS  # noqa: E402

PART_BY_DESIGN_ID = {design: part for part, design in DESIGN_IDS.items()}
# LDraw's color codes for the catalogue's 10 colors are their Rebrickable ids.
COLOR_BY_LDRAW_CODE = {code: color for color, code in REBRICKABLE_COLOR_IDS.items()}
THEMES = ("vehicles", "animals", "buildings")
DIFFICULTIES = ("easy", "medium", "hard")
LOCALES = ("en", "id")
MIN_PIECES, MAX_PIECES = 3, 15
MIN_STEPS, MAX_STEPS = 2, 5

PREVIEW_SIZE = 640
RENDER_SIZE = 1280  # rendered at twice the size, then cropped and downscaled
AZIMUTH = -135.0  # from the build's front left: BrickLayer's front is -y
ELEVATION = 30.0
WEBP_QUALITY = 88


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_meta(build_id: str, meta: dict) -> list[str]:
    problems = []
    if meta.get("theme") not in THEMES:
        problems.append(f"theme must be one of {THEMES}")
    if meta.get("difficulty") not in DIFFICULTIES:
        problems.append(f"difficulty must be one of {DIFFICULTIES}")
    for field in ("title", "summary"):
        if not all(str(meta.get(field, {}).get(locale, "")).strip() for locale in LOCALES):
            problems.append(f"{field} needs en and id")
    for locale in LOCALES:
        steps = meta.get("steps", {}).get(locale, [])
        if not MIN_STEPS <= len(steps) <= MAX_STEPS or not all(s.strip() for s in steps):
            problems.append(f"steps.{locale} needs {MIN_STEPS} to {MAX_STEPS} steps")
    return [f"{build_id}: {problem}" for problem in problems]


def parts_of(build) -> tuple[list[dict], list[str]]:
    """The build's parts as catalogue lines, sorted like an inventory, and any problems."""
    counts: Counter[tuple[str, str]] = Counter()
    problems = []
    for element, quantity in build.list_elements().items():
        part = PART_BY_DESIGN_ID.get(element.ldraw_id)
        color = COLOR_BY_LDRAW_CODE.get(element.color.ldraw_id)
        if part is None or color is None:
            problems.append(f"{element.ldraw_id} in {element.color.name} isn't in the catalogue")
            continue
        counts[part, color] += quantity
    lines = [{"part": p, "color": c, "quantity": q} for (p, c), q in counts.items()]
    lines.sort(key=lambda line: (-line["quantity"], line["part"], line["color"]))
    return lines, problems


def ldraw(build, meta: dict, build_id: str) -> str:
    from bricklayer import write_ldraw

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.ldr"
        write_ldraw(build, path)
        body = path.read_text()
    header = (
        f"0 {meta['title']['en']}\n"
        f"0 Name: {build_id}.ldr\n"
        "0 Author: Legoin, built with BrickLayer\n"
        "0 // Parts: the LDraw parts library, CC BY 4.0, https://library.ldraw.org\n"
        "0 // LEGO is a trademark of the LEGO Group, which does not sponsor, authorize or\n"
        "0 // endorse this file.\n"
    )
    return header + body


class Renderer:
    """An isometric, instruction-booklet view: a fixed key light and LDraw's edge lines."""

    def __init__(self):
        import pygfx as gfx

        gfx.renderers.wgpu.enable_wgpu_features("!float32-filterable")
        from rendercanvas.offscreen import RenderCanvas

        self.gfx = gfx
        self.canvas = RenderCanvas(size=(RENDER_SIZE, RENDER_SIZE), pixel_ratio=1)
        self.renderer = gfx.renderers.WgpuRenderer(self.canvas)

    def render(self, ldr: Path, out: Path, azimuth: float, scratch: Path) -> None:
        import numpy as np
        from brickwright.tools.software_rendering import angle_to_view_dir
        from ldraw2mesh import convert
        from PIL import Image

        gfx = self.gfx
        glb = scratch / f"{out.parent.name}.glb"
        convert(ldr, glb, gaps=True)
        model = gfx.load_gltf(str(glb)).scene
        view = np.array(angle_to_view_dir(azimuth, ELEVATION))
        view /= np.linalg.norm(view)
        right = np.cross(view, [0.0, 1.0, 0.0])
        right /= np.linalg.norm(right)
        up = np.cross(right, view)

        scene = gfx.Scene()
        scene.add(model)
        positions, colors = edge_lines(ldr, offset=-view * 0.6 * 0.0004)
        scene.add(
            gfx.Line(
                gfx.Geometry(positions=positions, colors=colors),
                gfx.LineSegmentMaterial(thickness=1.6, color_mode="vertex", aa=True),
            )
        )
        scene.add(gfx.AmbientLight(intensity=1.1))
        key = gfx.DirectionalLight(intensity=2.0)
        key.local.position = tuple(-view * 2 - right * 1.2 + up * 2.0)
        fill = gfx.DirectionalLight(intensity=0.5)
        fill.local.position = tuple(-view * 2 + right * 2.0)
        scene.add(key, fill)
        camera = gfx.OrthographicCamera()
        scene.add(camera)
        camera.show_object(model, view_dir=tuple(view), up=(0, 1, 0))
        self.canvas.request_draw(lambda: self.renderer.render(scene, camera))
        image = Image.fromarray(np.asarray(self.canvas.draw()), "RGBA")

        # Crop to the build, then centre it on a square with a margin.
        image = image.crop(image.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox())
        side = int(max(image.size) * 1.1)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
        square = square.resize((PREVIEW_SIZE, PREVIEW_SIZE), Image.Resampling.LANCZOS)
        square.save(out, "WEBP", quality=WEBP_QUALITY, method=6)


def edge_lines(ldr: Path, offset):
    """LDraw's edge lines in glTF space, nudged toward the camera so faces don't hide them."""
    import numpy as np
    from brickwright.tools.software_rendering import _ldraw_colors
    from ldraw2mesh import load_scene
    from ldraw2mesh.scene import build_jobs
    from ldraw2mesh.transform import LDRAW_TO_GLTF

    palette = _ldraw_colors()
    positions, colors = [], []
    for job in build_jobs(load_scene(ldr, gaps=True)):
        if not len(job.hard_edges):
            continue
        rgb = np.array(palette.get(job.color, ((0.5, 0.5, 0.5), 1.0))[0])
        dark = rgb @ [0.2126, 0.7152, 0.0722] < 0.12
        edge = np.array([0.42, 0.45, 0.5]) if dark else rgb * 0.35
        local = job.positions[job.hard_edges.reshape(-1)]
        homogeneous = np.c_[local, np.ones(len(local))].T
        for transform in job.transforms:
            world = (LDRAW_TO_GLTF @ transform @ homogeneous).T[:, :3]
            positions.append(world + offset)
            colors.append(np.tile(np.r_[edge, 1.0], (len(world), 1)))
    return np.concatenate(positions).astype("f4"), np.concatenate(colors).astype("f4")


def author(build_id: str, render, scratch: Path) -> tuple[dict, list[str]]:
    """Checks one build and, with a `render` callback, writes its model and preview."""
    from brickwright.tools.build_execution import load_build_module
    from brickwright.tools.validation import build_and_validate

    directory = BUILDS / build_id
    source_path = directory / "build.py"
    source = source_path.read_text()
    meta = load_build_module(str(source_path), source).META
    build, report = build_and_validate(str(source_path), source)
    problems = check_meta(build_id, meta)
    if not report.ok or build is None:
        failed = report.build_error or ", ".join(report.failed_checks)
        return {}, [*problems, f"{build_id}: validation failed: {failed}"]
    if "connected" not in {check.name for check in report.checks} or not build.is_connected():
        problems.append(f"{build_id}: run_tests() must check it's one connected assembly")
    parts, wrong = parts_of(build)
    problems += [f"{build_id}: {problem}" for problem in wrong]
    pieces = sum(line["quantity"] for line in parts)
    if not MIN_PIECES <= pieces <= MAX_PIECES:
        problems.append(f"{build_id}: {pieces} pieces, not {MIN_PIECES} to {MAX_PIECES}")
    if problems:
        return {}, problems

    model = directory / "model.ldr"
    preview = directory / "preview.webp"
    if render:
        model.write_text(ldraw(build, meta, build_id))
        render(model, preview, meta.get("azimuth", AZIMUTH), scratch)
    entry = {
        "id": build_id,
        "theme": meta["theme"],
        "difficulty": meta["difficulty"],
        "title": meta["title"],
        "summary": meta["summary"],
        "steps": meta["steps"],
        "parts": parts,
        "files": {
            "source": {"path": f"{build_id}/build.py", "sha256": sha256(source_path)},
            "model": {"path": f"{build_id}/model.ldr", "sha256": sha256(model)},
            "preview": {"path": f"{build_id}/preview.webp", "sha256": sha256(preview)},
        },
    }
    return entry, []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "ids", nargs="*", help="builds to export and render again, whether changed or not"
    )
    parser.add_argument("--force", action="store_true", help="export and render every build")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate every build and compare with the manifest, writing nothing",
    )
    args = parser.parse_args()

    every = sorted(path.parent.name for path in BUILDS.glob("*/build.py"))
    unknown = set(args.ids) - set(every)
    if unknown:
        parser.error(f"no such builds: {', '.join(sorted(unknown))}")
    old = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"builds": []}
    entries = {entry["id"]: entry for entry in old["builds"]}

    # llvmpipe's renders differ by a few pixels from run to run, so a build whose source and
    # files still match the manifest keeps them, and its preview doesn't churn.
    def changed(build_id: str) -> bool:
        files = entries.get(build_id, {}).get("files", {})
        return not files or any(
            not (BUILDS / file["path"]).exists() or sha256(BUILDS / file["path"]) != file["sha256"]
            for file in files.values()
        )

    if args.check:
        redo = set()
    elif args.force:
        redo = set(every)
    else:
        redo = set(args.ids) | {build_id for build_id in every if changed(build_id)}
    renderer = Renderer() if redo else None
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        for build_id in every:
            render = renderer.render if renderer and build_id in redo else None
            entry, found = author(build_id, render, Path(scratch))
            problems += found
            if found:
                continue
            if args.check and entries.get(build_id) != entry:
                problems.append(f"{build_id}: the manifest is out of date; run this script")
            entries[build_id] = entry
            pieces = sum(line["quantity"] for line in entry["parts"])
            print(f"ok  {build_id}: {pieces} pieces{', rendered' if render else ''}")

    for problem in problems:
        print(f"ERR {problem}", file=sys.stderr)
    if problems:
        return 1
    if not args.check:
        manifest = {
            "about": (
                "Written by scripts/builds.py with bricklayer-sdk 0.1.0 and brickwright 0.1.0. "
                "Don't edit by hand."
            ),
            "builds": [entries[build_id] for build_id in every],
        }
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print(f"wrote {MANIFEST.relative_to(ROOT)} with {len(every)} builds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
