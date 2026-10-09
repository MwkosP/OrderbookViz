"""Application entrypoint."""

from __future__ import annotations

import sys

from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtWidgets import QApplication

from modernglp.app.window import MainWindow
from modernglp.gl.widget import make_gl_format


def main() -> int:
    QSurfaceFormat.setDefaultFormat(make_gl_format())
    app = QApplication(sys.argv)
    app.setApplicationName("ModernGLp")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
