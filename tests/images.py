"""Small photos made on the fly, so the tests need no image files."""

import io
import random
import struct
import zlib

from PIL import Image, ImageDraw


def encode(image: Image.Image, format: str = "PNG", **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format, **options)
    return buffer.getvalue()


def pieces(width: int = 600, height: int = 400) -> Image.Image:
    """A light table with a few colored bricks on it."""
    image = Image.new("RGB", (width, height), (235, 232, 225))
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 50, 200, 140), fill=(201, 26, 9))
    draw.rectangle((300, 200, 380, 330), fill=(0, 85, 191))
    draw.rectangle((450, 60, 540, 120), fill=(242, 205, 55))
    return image


def flat(color: tuple[int, int, int], size: tuple[int, int] = (400, 300)) -> Image.Image:
    return Image.new("RGB", size, color)


def empty_table(size: tuple[int, int] = (400, 300)) -> Image.Image:
    """Sharp but empty: a gray table with fine grain and nothing on it."""
    rng = random.Random(0)
    image = Image.new("RGB", size)
    image.putdata(
        [(v, v, v) for v in (120 + rng.randint(-10, 10) for _ in range(size[0] * size[1]))]
    )
    return image


def soft_gradient(size: tuple[int, int] = (400, 300)) -> Image.Image:
    """Shapes without detail, like a photo out of focus: a smooth gray ramp."""
    width, height = size
    ramp = bytes(100 + 70 * x // (width - 1) for x in range(width))
    return Image.frombytes("L", size, ramp * height).convert("RGB")


def huge_png(width: int, height: int) -> bytes:
    """A valid black-and-white PNG of any size, a few KB on disk: a decompression bomb.

    Written by hand, so making it never holds the decoded image in memory.
    """

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    # One bit per pixel, grayscale; each row starts with filter type 0.
    header = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
    row = b"\x00" * (1 + (width + 7) // 8)
    compressor = zlib.compressobj(9)
    pixels = b"".join(compressor.compress(row) for _ in range(height)) + compressor.flush()
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    )
