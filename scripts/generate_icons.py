"""Generate checked-in raster icon derivatives from the SVG source mark."""
from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "desktop" / "assets" / "icons" / "habitreserve.svg"
WEB_ICONS = ROOT / "web" / "static" / "icons"


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(str(SOURCE)).render(painter)
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def write_ico(path: Path, sizes: tuple[int, ...]) -> None:
    images = [(size, png_bytes(render(size))) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = bytearray()
    offset = 6 + 16 * len(images)
    for size, data in images:
        dimension = 0 if size >= 256 else size
        entries.extend(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    path.write_bytes(header + entries + b"".join(data for _, data in images))


if __name__ == "__main__":
    WEB_ICONS.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        render(size).save(str(WEB_ICONS / f"habitreserve-{size}.png"), "PNG")
    write_ico(ROOT / "desktop" / "assets" / "icons" / "habitreserve.ico", (16, 24, 32, 48, 64, 128, 256))
