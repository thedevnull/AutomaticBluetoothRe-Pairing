#!/usr/bin/env python3
"""
Draws the program icon (blue circle with the Bluetooth rune) and saves it as
app_icon.ico and app_icon.png next to this file.

build_windows.bat runs it automatically. It is optional: if it fails, the
exe is simply built with the default icon.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import (QColor, QGuiApplication, QImage, QPainter,
                               QPainterPath, QPen)
except ImportError:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import (QColor, QGuiApplication, QImage, QPainter,
                             QPainterPath, QPen)


def draw(size):
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#1976d2"))
    p.drawEllipse(2, 2, 60, 60)
    pen = QPen(QColor("white"))
    pen.setWidth(5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(23, 23)
    for x, y in ((41, 41), (32, 50), (32, 14), (41, 23), (23, 41)):
        path.lineTo(x, y)
    p.drawPath(path)
    p.end()
    return img


def main():
    app = QGuiApplication(sys.argv[:1])   # noqa: F841  (needed by Qt)
    here = os.path.dirname(os.path.abspath(__file__))
    image = draw(256)
    ok = True
    for name in ("app_icon.png", "app_icon.ico"):
        saved = image.save(os.path.join(here, name))
        print(("saved " if saved else "FAILED to save ") + name)
        ok = ok and saved
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
