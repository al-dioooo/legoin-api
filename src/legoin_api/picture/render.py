"""Renders picture builds on a CPU, as `scripts/builds.py` renders the library (ADR-0010).
Needs the `picture` extra and the LDraw parts library (`LDRAW_LIBRARY_PATH`).

pygfx on wgpu, with an orthographic camera, a fixed key light and LDraw's edge lines. With no
GPU, wgpu falls back to llvmpipe, which lacks `float32-filterable`, so it's turned off.

- `preview`: the isometric picture people see, 640 px square WebP on a transparent background,
  from the library previews' angle;
- `views`: labelled views on white, tiled two wide, for Claude to compare with the picture.
"""

import io
import math
import tempfile
from pathlib import Path

import numpy as np

from legoin_api.picture.checks import ldraw
from legoin_api.picture.grid import Piece

PREVIEW_SIZE = 640
RENDER_SIZE = 1024
TILE = 384
WEBP_QUALITY = 88

VIEWS = {
    # name: (label, azimuth, elevation), in BrickWright's convention: azimuth 180 looks at the
    # model's front (from −y), positive azimuths swing the camera to the model's right (−x).
    "front": ("front (from -y, x to the right)", 180.0, 0.0),
    "back": ("back (from +y)", 0.0, 0.0),
    "left": ("left (from -x, front on the right)", 90.0, 0.0),
    "right": ("right (from +x, front on the left)", -90.0, 0.0),
    "top": ("top (front at the bottom, x to the right)", 0.0, 89.9),
    "iso": ("iso (from the front right, above)", -135.0, 30.0),
}
PREVIEW_VIEW = "iso"


class Renderer:
    def __init__(self, size: int = RENDER_SIZE):
        import pygfx as gfx

        gfx.renderers.wgpu.enable_wgpu_features("!float32-filterable")
        from rendercanvas.offscreen import RenderCanvas

        self.gfx = gfx
        self.canvas = RenderCanvas(size=(size, size), pixel_ratio=1)
        self.renderer = gfx.renderers.WgpuRenderer(self.canvas)

    def preview(self, pieces: tuple[Piece, ...], title: str) -> bytes:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            ldr, model = self._load(pieces, title, Path(tmp))
            image = self._draw(model, ldr, *VIEWS[PREVIEW_VIEW][1:])
        square = _square(image).resize((PREVIEW_SIZE, PREVIEW_SIZE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        square.save(out, "WEBP", quality=WEBP_QUALITY, method=6)
        return out.getvalue()

    def views(self, pieces: tuple[Piece, ...], views: list[str]) -> bytes:
        from PIL import Image, ImageDraw

        tiles = []
        with tempfile.TemporaryDirectory() as tmp:
            ldr, model = self._load(pieces, "render", Path(tmp))
            for name in views:
                label, azimuth, elevation = VIEWS[name]
                image = _square(self._draw(model, ldr, azimuth, elevation))
                image = image.resize((TILE - 16, TILE - 16), Image.Resampling.LANCZOS)
                tile = Image.new("RGB", (TILE, TILE), "white")
                tile.paste(image, (8, 8), image)
                ImageDraw.Draw(tile).text((8, 4), label, fill=(60, 60, 60))
                tiles.append(tile)
        columns = min(2, len(tiles))
        rows = -(-len(tiles) // columns)
        gap = 6
        size = (columns * TILE + (columns - 1) * gap, rows * TILE + (rows - 1) * gap)
        sheet = Image.new("RGB", size, (200, 200, 200))
        for i, tile in enumerate(tiles):
            sheet.paste(tile, ((i % columns) * (TILE + gap), (i // columns) * (TILE + gap)))
        out = io.BytesIO()
        sheet.save(out, "PNG", optimize=True)
        return out.getvalue()

    def _load(self, pieces: tuple[Piece, ...], title: str, scratch: Path):
        from ldraw2mesh import convert

        ldr = scratch / "model.ldr"
        ldr.write_text(ldraw(pieces, title))
        glb = scratch / "model.glb"
        convert(ldr, glb, gaps=True)
        return ldr, self.gfx.load_gltf(str(glb)).scene

    def _draw(self, model, ldr: Path, azimuth: float, elevation: float):
        from PIL import Image

        gfx = self.gfx
        view = np.array(angle_to_view_dir(azimuth, elevation))
        view /= np.linalg.norm(view)
        up_hint = np.array([0.0, 1.0, 0.0] if abs(elevation) < 89 else [0.0, 0.0, -1.0])
        right = np.cross(view, up_hint)
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
        camera.show_object(model, view_dir=tuple(view), up=tuple(up_hint))
        self.canvas.request_draw(lambda: self.renderer.render(scene, camera))
        image = Image.fromarray(np.asarray(self.canvas.draw()), "RGBA")
        box = image.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
        return image.crop(box) if box else image


def _square(image):
    """The image centred on a transparent square, with a margin."""
    from PIL import Image

    side = max(1, int(max(image.size) * 1.1))
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    return square


def angle_to_view_dir(azimuth: float, elevation: float) -> tuple[float, float, float]:
    """The camera's view direction in glTF space, as BrickWright's `angle_to_view_dir`."""
    a, e = math.radians(azimuth), math.radians(elevation)
    return (math.cos(e) * math.sin(a), -math.sin(e), math.cos(e) * math.cos(a))


def _ldraw_colors() -> dict[int, tuple[tuple[float, ...], float]]:
    """LDraw code to (RGB from 0 to 1, alpha), from BrickLayer's colors, as BrickWright's."""
    from bricklayer import Color
    from bricklayer.colors import hex2rgb

    return {color.ldraw_id: (tuple(c / 255 for c in hex2rgb(color.rgb)), 1.0) for color in Color}


def edge_lines(ldr: Path, offset):
    """LDraw's edge lines in glTF space, nudged toward the camera so faces don't hide them.
    The same as `scripts/builds.py`."""
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
