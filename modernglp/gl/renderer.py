"""ModernGL depth-chart renderer."""

from __future__ import annotations

from pathlib import Path

import moderngl
import numpy as np

from modernglp.orderbook.model import OrderBook

_SHADER_DIR = Path(__file__).with_name("shaders")


def _load(name: str) -> str:
    return (_SHADER_DIR / name).read_text(encoding="utf-8")


class DepthChartRenderer:
    """Renders cumulative bid/ask depth as filled step areas."""

    BID_COLOR = (0.20, 0.72, 0.45, 0.55)
    ASK_COLOR = (0.90, 0.28, 0.32, 0.55)
    BID_EDGE = (0.35, 0.95, 0.60, 0.95)
    ASK_EDGE = (1.00, 0.45, 0.48, 0.95)
    GRID_COLOR = (1.0, 1.0, 1.0, 0.08)
    MID_COLOR = (1.0, 1.0, 1.0, 0.35)
    BG = (0.07, 0.08, 0.10, 1.0)

    def __init__(self, ctx: moderngl.Context) -> None:
        self.ctx = ctx
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA

        self._prog = ctx.program(vertex_shader=_load("depth.vert"), fragment_shader=_load("depth.frag"))
        self._line_prog = ctx.program(vertex_shader=_load("line.vert"), fragment_shader=_load("line.frag"))

        # Dynamic VBO capacity for filled area + outline vertices.
        self._vbo = ctx.buffer(reserve=64 * 1024)
        self._vao = ctx.vertex_array(self._prog, [(self._vbo, "2f", "in_position")])

        self._line_vbo = ctx.buffer(reserve=8 * 1024)
        self._line_vao = ctx.vertex_array(self._line_prog, [(self._line_vbo, "2f", "in_position")])

        self._grid = self._build_grid()

    def _build_grid(self) -> moderngl.VertexArray:
        lines: list[float] = []
        for i in range(1, 5):
            x = -0.92 + i * (1.84 / 5)
            lines.extend([x, -0.88, x, 0.88])
        for i in range(1, 4):
            y = -0.88 + i * (1.76 / 4)
            lines.extend([-0.92, y, 0.92, y])
        buf = self.ctx.buffer(np.array(lines, dtype=np.float32).tobytes())
        return self.ctx.vertex_array(self._line_prog, [(buf, "2f", "in_position")])

    def render(self, book: OrderBook, width: int, height: int) -> None:
        self.ctx.viewport = (0, 0, max(width, 1), max(height, 1))
        self.ctx.clear(*self.BG)

        self._line_prog["u_color"].value = self.GRID_COLOR
        self._grid.render(moderngl.LINES)

        bid_p, bid_c, ask_p, ask_c = book.depth_arrays()
        if bid_p.size == 0 and ask_p.size == 0:
            return

        prices = []
        if bid_p.size:
            prices.append(float(bid_p.min()))
            prices.append(float(bid_p.max()))
        if ask_p.size:
            prices.append(float(ask_p.min()))
            prices.append(float(ask_p.max()))
        price_min, price_max = min(prices), max(prices)
        pad = max((price_max - price_min) * 0.05, book.tick_size)
        price_min -= pad
        price_max += pad

        max_size = float(max(bid_c[-1] if bid_c.size else 0.0, ask_c[-1] if ask_c.size else 0.0, 1.0))

        self._prog["u_price_range"].value = (price_min, price_max)
        self._prog["u_max_size"].value = max_size

        if bid_p.size:
            fill, edge = self._step_mesh(bid_p, bid_c)
            self._draw_mesh(fill, self.BID_COLOR, moderngl.TRIANGLE_STRIP)
            self._draw_mesh(edge, self.BID_EDGE, moderngl.LINE_STRIP)

        if ask_p.size:
            fill, edge = self._step_mesh(ask_p, ask_c)
            self._draw_mesh(fill, self.ASK_COLOR, moderngl.TRIANGLE_STRIP)
            self._draw_mesh(edge, self.ASK_EDGE, moderngl.LINE_STRIP)

        mid = book.mid()
        if mid is not None:
            x = (mid - price_min) / max(price_max - price_min, 1e-6)
            ndc_x = -0.92 + 1.84 * min(max(x, 0.0), 1.0)
            mid_line = np.array([ndc_x, -0.88, ndc_x, 0.88], dtype=np.float32)
            self._line_vbo.write(mid_line.tobytes())
            self._line_prog["u_color"].value = self.MID_COLOR
            self._line_vao.render(moderngl.LINES, vertices=2)

    def _step_mesh(self, prices: np.ndarray, cum: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Build a triangle-strip fill and line-strip outline for a step depth curve."""
        # Step: for each level, draw horizontal then vertical to next.
        edge_pts: list[tuple[float, float]] = [(float(prices[0]), 0.0)]
        for i, (p, c) in enumerate(zip(prices, cum)):
            edge_pts.append((float(p), float(c)))
            if i + 1 < len(prices):
                edge_pts.append((float(prices[i + 1]), float(c)))

        edge = np.array(edge_pts, dtype=np.float32)

        # Triangle strip alternating baseline (y=0) and curve points.
        fill = np.empty((len(edge) * 2, 2), dtype=np.float32)
        fill[0::2, 0] = edge[:, 0]
        fill[0::2, 1] = 0.0
        fill[1::2] = edge
        return fill, edge

    def _draw_mesh(self, vertices: np.ndarray, color: tuple[float, float, float, float], mode: int) -> None:
        data = vertices.astype(np.float32).tobytes()
        if len(data) > self._vbo.size:
            self._vbo.orphan(len(data))
        self._vbo.write(data)
        self._prog["u_color"].value = color
        self._vao.render(mode, vertices=len(vertices))
