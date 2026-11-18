"""
Make the CortexUI icons from the logo (assets/logo.png): assets/icon.png, assets/icon.ico and
(on macOS) assets/icon.icns.

    python packaging/make_icons.py

The logo is a square; each icon shows it as a rounded tile, inside Apple's icon grid on macOS
(so it matches the size of other Dock icons) and almost edge to edge on Windows. Below 128 px the
logo's thin line would fade to a smear, so it is drawn bolder there (about 1.5 px at any size).
The outputs are committed; run this only when the logo changes.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPainterPath  # noqa: E402

ASSETS = os.path.join(ROOT, "assets")
LOGO = os.path.join(ASSETS, "logo.png")
LINE = 18 / 1254  # the logo's line width, as a fraction of its side
MIN_LINE_PX = 1.6  # thinnest line drawn in an icon
WORK = 128  # small icons are made bolder at this size, then scaled down

# Empty margin around the tile: macOS draws icons inside a 824/1024 grid; Windows uses the
# whole square
MAC_MARGIN = 100 / 1024
WINDOWS_MARGIN = 24 / 1024
CORNER = 0.225  # corner radius, as a fraction of the tile

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
ICONSET = {  # macOS .iconset file name -> pixel size
    "icon_16x16.png": 16, "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512, "icon_512x512@2x.png": 1024,
}


def scaled(img: QImage, side: int) -> QImage:
    return img.scaled(side, side, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def bolder(img: QImage, radius: int) -> QImage:
    """The bright line widened by radius pixels (the brightest pixel within that distance)."""
    out = img.copy()
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Lighten)
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if (dx or dy) and dx * dx + dy * dy <= radius * radius:
                p.drawImage(dx, dy, img)
    p.end()
    return out


def art(logo: QImage, side: int) -> QImage:
    """The logo at side x side pixels, its line at least MIN_LINE_PX wide."""
    if side >= WORK:
        return scaled(logo, side)
    want = MIN_LINE_PX * WORK / side  # line width needed at WORK px
    radius = max(0, round((want - LINE * WORK) / 2))
    return scaled(bolder(scaled(logo, WORK), radius), side)


def tile(logo: QImage, size: int, margin: float) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    m = round(size * margin)
    side = size - 2 * m
    rect = QRectF(m, m, side, side)
    path = QPainterPath()
    path.addRoundedRect(rect, side * CORNER, side * CORNER)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setClipPath(path)
    p.drawImage(rect, art(logo, side))
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
    logo = QImage(LOGO).convertToFormat(QImage.Format.Format_ARGB32)
    if logo.isNull():
        sys.exit(f"cannot read {LOGO}")

    tile(logo, 1024, WINDOWS_MARGIN).save(os.path.join(ASSETS, "icon.png"))
    write_ico(os.path.join(ASSETS, "icon.ico"), [tile(logo, s, WINDOWS_MARGIN) for s in ICO_SIZES])
    written = ["icon.png", "icon.ico"]

    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = os.path.join(tmp, "icon.iconset")
            os.makedirs(iconset)
            for name, size in ICONSET.items():
                tile(logo, size, MAC_MARGIN).save(os.path.join(iconset, name))
            subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(ASSETS, "icon.icns")],
                           check=True)
        written.append("icon.icns")
    else:
        print("iconutil not found (macOS only): assets/icon.icns not updated")

    print("wrote", ", ".join(f"assets/{n}" for n in written))


if __name__ == "__main__":
    main()
