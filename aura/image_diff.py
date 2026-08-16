from __future__ import annotations

from .errors import AuraError

import struct
import zlib
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Bytes per pixel for the colour types this decoder handles at bit depth 8.
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


class UnsupportedImage(AuraError, ValueError):
    """Raised for PNG variants this dependency-free decoder cannot read."""


class DecodedImage:
    __slots__ = ("width", "height", "channels", "pixels")

    def __init__(self, width: int, height: int, channels: int, pixels: bytes) -> None:
        self.width = width
        self.height = height
        self.channels = channels
        self.pixels = pixels

    def rgb(self, x: int, y: int) -> tuple[int, int, int]:
        index = (y * self.width + x) * self.channels
        data = self.pixels
        if self.channels == 1:
            value = data[index]
            return value, value, value
        if self.channels == 2:
            value = data[index]
            return value, value, value
        return data[index], data[index + 1], data[index + 2]


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode_png(path: Path) -> DecodedImage:
    """Decode a non-interlaced 8-bit PNG using only the standard library."""
    raw = Path(path).read_bytes()
    if not raw.startswith(PNG_SIGNATURE):
        raise UnsupportedImage(f"{Path(path).name} is not a PNG file")
    offset = len(PNG_SIGNATURE)
    header: tuple | None = None
    compressed = bytearray()
    while offset + 8 <= len(raw):
        length, tag = struct.unpack(">I4s", raw[offset:offset + 8])
        body = raw[offset + 8:offset + 8 + length]
        offset += 12 + length  # length + tag + data + crc
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            compressed += body
        elif tag == b"IEND":
            break
    if header is None:
        raise UnsupportedImage("PNG header is missing")
    width, height, depth, colour, compression, filtering, interlace = header
    if depth != 8:
        raise UnsupportedImage(f"only 8-bit PNGs are supported, not {depth}-bit")
    if colour not in CHANNELS:
        raise UnsupportedImage(f"PNG colour type {colour} is not supported")
    if compression != 0 or filtering != 0:
        raise UnsupportedImage("unsupported PNG compression or filter method")
    if interlace != 0:
        raise UnsupportedImage("interlaced PNGs are not supported")
    if not width or not height:
        raise UnsupportedImage("PNG has no pixels")

    channels = CHANNELS[colour]
    stride = width * channels
    data = zlib.decompress(bytes(compressed))
    expected = (stride + 1) * height
    if len(data) < expected:
        raise UnsupportedImage("PNG pixel data is truncated")

    out = bytearray(stride * height)
    previous = bytearray(stride)
    position = 0
    for row in range(height):
        filter_type = data[position]
        position += 1
        line = bytearray(data[position:position + stride])
        position += stride
        if filter_type == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                upper_left = previous[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, previous[i], upper_left)) & 0xFF
        elif filter_type != 0:
            raise UnsupportedImage(f"unknown PNG row filter {filter_type}")
        out[row * stride:(row + 1) * stride] = line
        previous = line
    return DecodedImage(width, height, channels, bytes(out))


def compare_images(first: Path, second: Path, *, tolerance: int = 8) -> dict:
    """Compare two PNGs pixel by pixel and describe exactly how they differ.

    Deterministic on purpose: the result is measured evidence, not a model's
    impression of whether two screenshots look alike.
    """
    left = decode_png(first)
    right = decode_png(second)
    if (left.width, left.height) != (right.width, right.height):
        return {
            "identical": False, "reason": "size",
            "first_size": [left.width, left.height],
            "second_size": [right.width, right.height],
            "summary": (f"Different dimensions: {left.width}x{left.height} versus "
                        f"{right.width}x{right.height}."),
        }

    limit = max(0, int(tolerance))
    changed = 0
    min_x, min_y, max_x, max_y = left.width, left.height, -1, -1
    left_channels, right_channels = left.channels, right.channels
    left_stride = left.width * left_channels
    right_stride = right.width * right_channels
    left_pixels, right_pixels = left.pixels, right.pixels
    same_layout = left_channels == right_channels

    for y in range(left.height):
        left_row = left_pixels[y * left_stride:(y + 1) * left_stride]
        right_row = right_pixels[y * right_stride:(y + 1) * right_stride]
        # Whole rows are usually untouched between two renders, and comparing
        # them as bytes skips the per-pixel work for those rows entirely.
        if same_layout and left_row == right_row:
            continue
        for x in range(left.width):
            a = x * left_channels
            b = x * right_channels
            if left_channels >= 3:
                ar, ag, ab = left_row[a], left_row[a + 1], left_row[a + 2]
            else:
                ar = ag = ab = left_row[a]
            if right_channels >= 3:
                br, bg, bb = right_row[b], right_row[b + 1], right_row[b + 2]
            else:
                br = bg = bb = right_row[b]
            if abs(ar - br) > limit or abs(ag - bg) > limit or abs(ab - bb) > limit:
                changed += 1
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

    total = left.width * left.height
    ratio = changed / total if total else 0.0
    result = {
        "identical": changed == 0,
        "reason": "pixels",
        "width": left.width, "height": left.height,
        "changed_pixels": changed, "total_pixels": total,
        "changed_percent": round(ratio * 100, 3),
        "tolerance": limit,
    }
    if changed:
        result["changed_region"] = {
            "left": min_x, "top": min_y, "right": max_x, "bottom": max_y,
            "width": max_x - min_x + 1, "height": max_y - min_y + 1,
        }
        result["summary"] = (
            f"{result['changed_percent']}% of pixels differ, inside a "
            f"{max_x - min_x + 1}x{max_y - min_y + 1} region starting at "
            f"({min_x}, {min_y}).")
    else:
        result["summary"] = "The two images are identical within the tolerance."
    return result
