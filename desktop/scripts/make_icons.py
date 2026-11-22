#!/usr/bin/env python3
"""Render the SYNCODE Desktop icons (PNG + ICO) with the standard library only.

The mark mirrors ``src-tauri/icons/syncode.svg``: two offset rounded bars on a
dark rounded square. Rendering uses 4x4 supersampling for anti-aliasing.
"""
from __future__ import annotations

from pathlib import Path
import struct
import zlib

OUT = Path(__file__).resolve().parents[1] / "src-tauri" / "icons"
BG = (0x16, 0x1A, 0x2E)
BARS = (((152, 300, 712, 496), (0x7D, 0x8C, 0xFF)), ((312, 528, 872, 724), (0x3F, 0xC1, 0xC3)))


def _inside_round_rect(x: float, y: float, box, radius: float) -> bool:
    x0, y0, x1, y1 = box
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + radius), x1 - radius)
    cy = min(max(y, y0 + radius), y1 - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def render(size: int) -> bytes:
    scale = 1024 / size
    samples = 4
    rows = []
    for py in range(size):
        row = bytearray([0])  # PNG filter: none
        for px in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(samples):
                for sx in range(samples):
                    x = (px + (sx + 0.5) / samples) * scale
                    y = (py + (sy + 0.5) / samples) * scale
                    if not _inside_round_rect(x, y, (0, 0, 1024, 1024), 224):
                        continue
                    color = BG
                    for box, fill in BARS:
                        if _inside_round_rect(x, y, box, (box[3] - box[1]) / 2):
                            color = fill
                    acc[0] += color[0]
                    acc[1] += color[1]
                    acc[2] += color[2]
                    acc[3] += 1
            n = samples * samples
            alpha = acc[3] / n
            if acc[3]:
                row += bytes(round(c / acc[3]) for c in acc[:3]) + bytes([round(alpha * 255)])
            else:
                row += b"\0\0\0\0"
        rows.append(bytes(row))
    return _png(size, b"".join(rows))


def _png(size: int, raw: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _ico(images: dict[int, bytes]) -> bytes:
    sizes = sorted(images)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for size in sizes:
        data = images[size]
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    return header + entries + blobs


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cache = {size: render(size) for size in (16, 24, 32, 48, 64, 128, 256)}
    (OUT / "32x32.png").write_bytes(cache[32])
    (OUT / "128x128.png").write_bytes(cache[128])
    (OUT / "128x128@2x.png").write_bytes(cache[256])
    (OUT / "icon.png").write_bytes(cache[256])
    (OUT / "icon.ico").write_bytes(_ico({s: cache[s] for s in (16, 24, 32, 48, 64, 256)}))
    print(f"Icons written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
