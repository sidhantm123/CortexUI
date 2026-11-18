"""
Draw the CortexUI icon and write assets/icon.png, assets/icon.ico and (on macOS) assets/icon.icns.

    python packaging/make_icons.py

Three channels on the app's dark background: the middle one, in the accent colour, fires a
spike (negative-going, as extracellular spikes are); the outer two are grey noise and are left
out below 48 px. Each size is drawn directly (not scaled down), so the trace stays visible at
16 px. The outputs are committed; run this only when the icon changes.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen  # noqa: E402

from ui.theme import ACCENT, BORDER, SURFACE, TEXT_SECONDARY  # noqa: E402

ASSETS = os.path.join(ROOT, "assets")

# The traces across the tile, x and y from 0 to 1 (y down). The spike: a deep trough, then a
# smaller rebound
SPIKE_TRACE = [
    (0.00, 0.50), (0.06, 0.46), (0.11, 0.53), (0.17, 0.48), (0.22, 0.54), (0.28, 0.47),
    (0.34, 0.52), (0.40, 0.48), (0.45, 0.51), (0.50, 0.80), (0.56, 0.32), (0.62, 0.55),
    (0.68, 0.47), (0.74, 0.53), (0.80, 0.48), (0.86, 0.53), (0.93, 0.47), (1.00, 0.51),
]
NOISE = [0.0, 0.6, -0.8, 0.3, -0.4, 1.0, -0.6, 0.2, -1.0, 0.7, -0.3, 0.5, -0.9, 0.4, -0.2, 0.8, -0.5, 0.1]
NOISE_TRACES = [  # (baseline, amplitude, offset into NOISE)
    (0.07, 0.035, 0),
    (0.93, 0.035, 7),
]

# Empty margin around the tile: macOS draws icons inside a 824/1024 grid; Windows uses the
# whole square
MAC_MARGIN = 100 / 1024
WINDOWS_MARGIN = 24 / 1024

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
ICONSET = {  # macOS .iconset file name -> pixel size
    "icon_16x16.png": 16, "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512, "icon_512x512@2x.png": 1024,
}


def draw(size: int, margin: float) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    m = size * margin
    side = size - 2 * m
    tile = QRectF(m, m, side, side)
    radius = side * 0.225
    p.setPen(QPen(QColor(BORDER), max(side / 256, 1.0)))
    p.setBrush(QColor(SURFACE))
    p.drawRoundedRect(tile.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

    # The trace area inside the tile
    inner = tile.adjusted(side * 0.13, side * 0.14, -side * 0.13, -side * 0.14)

    def point(x, y):
        return QPointF(inner.left() + x * inner.width(), inner.top() + y * inner.height())

    def trace(points, color, width):
        path = QPainterPath(point(*points[0]))
        for x, y in points[1:]:
            path.lineTo(point(x, y))
        pen = QPen(color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPath(path)

    p.setBrush(Qt.BrushStyle.NoBrush)
    if size >= 48:
        grey = QColor(TEXT_SECONDARY)
        grey.setAlphaF(0.5)
        xs = [i / (len(NOISE) - 1) for i in range(len(NOISE))]
        for base, amp, start in NOISE_TRACES:
            ys = [base + amp * NOISE[(i + start) % len(NOISE)] for i in range(len(NOISE))]
            trace(list(zip(xs, ys)), grey, max(side * 0.02, 1.0))
    trace(SPIKE_TRACE, QColor(ACCENT), max(side * 0.045, 1.6))
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


def write_ico(path: str, images):
    """An .ico of PNG images (Windows Vista and later read these), written without Pillow."""
    pngs = [(img.width(), png_bytes(img)) for img in images]
    offset = 6 + 16 * len(pngs)
    out = [struct.pack("<HHH", 0, 1, len(pngs))]
    for size, data in pngs:
        dim = 0 if size >= 256 else size  # 0 means 256
        out.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    out += [data for _, data in pngs]
    with open(path, "wb") as f:
        f.write(b"".join(out))


def main():
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (QPainter needs it)
    os.makedirs(ASSETS, exist_ok=True)

    draw(1024, WINDOWS_MARGIN).save(os.path.join(ASSETS, "icon.png"))
    write_ico(os.path.join(ASSETS, "icon.ico"), [draw(s, WINDOWS_MARGIN) for s in ICO_SIZES])
    written = ["icon.png", "icon.ico"]

    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = os.path.join(tmp, "icon.iconset")
            os.makedirs(iconset)
            for name, size in ICONSET.items():
                draw(size, MAC_MARGIN).save(os.path.join(iconset, name))
            subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(ASSETS, "icon.icns")],
                           check=True)
        written.append("icon.icns")
    else:
        print("iconutil not found (macOS only): assets/icon.icns not updated")

    print("wrote", ", ".join(f"assets/{n}" for n in written))


if __name__ == "__main__":
    main()
