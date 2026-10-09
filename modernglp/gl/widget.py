"""QOpenGLWidget host for ModernGL orderbook renderers."""

from __future__ import annotations

import os
from enum import Enum

import moderngl
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QSurfaceFormat
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

from modernglp.gl.l2_renderer import L2LadderRenderer
from modernglp.gl.renderer import DepthChartRenderer
from modernglp.orderbook.model import OrderBook


class ViewMode(str, Enum):
    L2 = "l2"
    DEPTH = "depth"


def make_gl_format() -> QSurfaceFormat:
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
    fmt.setSamples(4)
    return fmt


class OrderBookGLWidget(QOpenGLWidget):
    """Embeds ModernGL inside Qt's OpenGL widget lifecycle."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFormat(make_gl_format())
        self.setMinimumSize(640, 400)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._ctx: moderngl.Context | None = None
        self._depth: DepthChartRenderer | None = None
        self._l2: L2LadderRenderer | None = None
        self._book: OrderBook | None = None
        self._mode = ViewMode.L2

        self._font = QFont("IBM Plex Mono", 11)
        if not self._font.exactMatch():
            self._font = QFont("monospace", 11)
        self._header_font = QFont(self._font)
        self._header_font.setPointSize(10)
        self._title_font = QFont(self._font)
        self._title_font.setPointSize(12)
        self._title_font.setBold(True)

        self._levels = int(os.environ.get("MODERNGLP_LEVELS", "15"))
        self._bucket = float(os.environ.get("MODERNGLP_BUCKET", "2000"))

    def set_mode(self, mode: ViewMode) -> None:
        self._mode = mode
        self.update()

    def set_book(self, book: OrderBook) -> None:
        self._book = book
        self.update()

    def initializeGL(self) -> None:
        self._ctx = moderngl.create_context(require=330)
        self._depth = DepthChartRenderer(self._ctx)
        self._l2 = L2LadderRenderer(
            self._ctx, levels=self._levels, bucket=self._bucket
        )

    def resizeGL(self, w: int, h: int) -> None:
        if self._ctx is not None:
            self._ctx.viewport = (0, 0, max(w, 1), max(h, 1))

    def paintGL(self) -> None:
        if self._ctx is None:
            return

        fbo = self._ctx.detect_framebuffer(self.defaultFramebufferObject())
        fbo.use()

        if self._book is None:
            self._ctx.clear(0.09, 0.09, 0.09, 1.0)
            return

        if self._mode is ViewMode.L2 and self._l2 is not None:
            layout = self._l2.render(self._book, self.width(), self.height())
            self._paint_l2_labels(layout)
        elif self._depth is not None:
            self._depth.render(self._book, self.width(), self.height())

    def _paint_l2_labels(self, layout) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        for label in layout.labels:
            y = int(label.y - 9)

            if label.kind == "title":
                painter.setFont(self._title_font)
                painter.setPen(QColor("#e8eaed"))
                painter.drawText(
                    12, y - 4, self.width() - 24, 24,
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    label.text,
                )
                continue

            if label.kind == "header":
                painter.setFont(self._header_font)
                painter.setPen(QColor("#9aa0a6"))
                if label.text.startswith("Size"):
                    painter.drawText(
                        int(label.x - 60), y, 120, 18,
                        int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                        label.text,
                    )
                elif label.text.startswith("Bucket"):
                    painter.drawText(
                        int(label.x - 140), y, 140, 18,
                        int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                        label.text,
                    )
                else:
                    painter.drawText(
                        int(label.x), y, 80, 18,
                        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                        label.text,
                    )
                continue

            painter.setFont(self._font)

            if label.kind == "depth":
                painter.setPen(QColor("#f1f3f4"))
                painter.drawText(
                    int(label.x), y, 100, 18,
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    label.text,
                )
            elif label.kind == "size":
                painter.setPen(QColor("#f1f3f4"))
                painter.drawText(
                    int(label.x - 55), y, 110, 18,
                    int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                    label.text,
                )
            elif label.kind == "bucket_ask":
                painter.setPen(QColor("#f1f3f4"))
                painter.drawText(
                    int(label.x - 160), y, 160, 18,
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    label.text,
                )
            elif label.kind == "bucket_bid":
                painter.setPen(QColor("#f1f3f4"))
                painter.drawText(
                    int(label.x - 160), y, 160, 18,
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    label.text,
                )
            elif label.kind == "mid":
                painter.setPen(QColor("#e8eaed"))
                painter.drawText(
                    int(label.x - 280), y, 280, 18,
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    label.text,
                )

        painter.end()
