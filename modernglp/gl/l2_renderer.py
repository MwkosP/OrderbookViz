"""ModernGL L2 ladder matching Depth | Size | Bucket layout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import moderngl
import numpy as np

from modernglp.orderbook.model import OrderBook

_SHADER_DIR = Path(__file__).with_name("shaders")


def _load(name: str) -> str:
    return (_SHADER_DIR / name).read_text(encoding="utf-8")


def _fmt_size(size: float) -> str:
    if size >= 100:
        return f"{size:,.2f}"
    if size >= 10:
        return f"{size:.2f}"
    if size >= 1:
        return f"{size:.2f}"
    return f"{size:.2f}"


def _fmt_bucket(lo: float, bucket: float) -> str:
    hi = lo + bucket
    if bucket >= 1:
        return f"{lo:,.0f}–{hi:,.0f}"
    return f"{lo:.4f}–{hi:.4f}"


def _fmt_mid(price: float) -> str:
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.6f}"


def _base_asset(symbol: str) -> str:
    # "COINBASE BTC-USD" or "BTC-USD" → BTC
    token = symbol.split()[-1]
    return token.split("-")[0].split("/")[0]


@dataclass
class LadderLabel:
    x: float
    y: float
    text: str
    kind: str  # header | depth | size | bucket_ask | bucket_bid | mid | title


@dataclass
class LadderLayout:
    labels: list[LadderLabel]
    max_depth: float


class L2LadderRenderer:
    """Reference ladder: Depth (cum + left bars) | Size | Bucket (USD)."""

    BG = (0.09, 0.09, 0.09, 1.0)
    ASK_ROW = (0.28, 0.10, 0.10, 0.55)
    BID_ROW = (0.08, 0.22, 0.12, 0.55)
    ASK_BAR = (0.75, 0.22, 0.22, 0.85)
    BID_BAR = (0.18, 0.62, 0.32, 0.85)
    MID_BG = (0.16, 0.16, 0.16, 1.0)
    GRID = (1.0, 1.0, 1.0, 0.10)

    LEFT = -0.98
    RIGHT = 0.98
    TOP = 0.82
    BOTTOM = -0.94
    MID_GAP = 0.055

    # Columns (NDC): Depth | Size | Bucket
    DEPTH_LEFT = -0.98
    DEPTH_RIGHT = 0.12
    SIZE_LEFT = 0.12
    SIZE_RIGHT = 0.42
    BUCKET_LEFT = 0.42
    BUCKET_RIGHT = 0.98

    DEPTH_TEXT_X = -0.92
    SIZE_TEXT_X = 0.27
    BUCKET_TEXT_X = 0.96

    def __init__(
        self,
        ctx: moderngl.Context,
        *,
        levels: int = 15,
        bucket: float = 2000.0,
    ) -> None:
        self.ctx = ctx
        self.levels = levels
        self.bucket = bucket
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA

        self._prog = ctx.program(vertex_shader=_load("rect.vert"), fragment_shader=_load("rect.frag"))
        self._vbo = ctx.buffer(reserve=256 * 1024)
        self._vao = ctx.vertex_array(self._prog, [(self._vbo, "2f", "in_position")])
        self._last_layout = LadderLayout([], 1.0)

    @property
    def last_layout(self) -> LadderLayout:
        return self._last_layout

    def render(self, book: OrderBook, width: int, height: int) -> LadderLayout:
        self.ctx.viewport = (0, 0, max(width, 1), max(height, 1))
        self.ctx.clear(*self.BG)

        ask_lo, ask_sz, ask_dp, bid_lo, bid_sz, bid_dp = book.bucket_ladder(
            self.bucket, self.levels
        )
        n_ask, n_bid = int(ask_lo.size), int(bid_lo.size)

        labels: list[LadderLabel] = []
        base = _base_asset(book.symbol)
        title = (
            f"Live {book.symbol} Ladder (L2)  "
            f"[top {self.levels}]  [bucket {self.bucket:,.0f}]"
        )
        labels.append(
            LadderLabel(*self._ndc_to_screen(0.0, 0.94, width, height), title, "title")
        )

        hy = self.TOP + 0.06
        labels.append(LadderLabel(*self._ndc_to_screen(self.DEPTH_TEXT_X, hy, width, height), "Depth", "header"))
        labels.append(LadderLabel(*self._ndc_to_screen(self.SIZE_TEXT_X, hy, width, height), f"Size ({base})", "header"))
        labels.append(LadderLabel(*self._ndc_to_screen(self.BUCKET_TEXT_X, hy, width, height), "Bucket (USD)", "header"))

        if n_ask == 0 and n_bid == 0:
            self._last_layout = LadderLayout(labels, 1.0)
            return self._last_layout

        max_depth = float(
            max(
                float(ask_dp[-1]) if n_ask else 0.0,
                float(bid_dp[-1]) if n_bid else 0.0,
                1e-12,
            )
        )

        row_h = self._row_height(n_bid, n_ask)
        verts: list[float] = []
        colors: list[tuple[tuple[float, float, float, float], int]] = []
        bar_span = self.DEPTH_RIGHT - self.DEPTH_LEFT

        # Column rules
        for x in (self.SIZE_LEFT, self.BUCKET_LEFT):
            self._quad(verts, x - 0.002, self.BOTTOM, x + 0.002, self.TOP + 0.02)
            colors.append((self.GRID, 6))

        for i in range(n_ask):
            y1, y0 = self._ask_row_y(i, row_h)
            cy = 0.5 * (y0 + y1)
            depth = float(ask_dp[i])
            size = float(ask_sz[i])
            t = min(depth / max_depth, 1.0)

            self._quad(verts, self.LEFT, y0, self.RIGHT, y1)
            colors.append((self.ASK_ROW, 6))

            bar_left = self.DEPTH_RIGHT - bar_span * t
            self._quad(verts, bar_left, y0 + 0.06 * row_h, self.DEPTH_RIGHT, y1 - 0.06 * row_h)
            colors.append((self.ASK_BAR, 6))

            labels.append(
                LadderLabel(*self._ndc_to_screen(self.DEPTH_TEXT_X, cy, width, height), _fmt_size(depth), "depth")
            )
            labels.append(
                LadderLabel(*self._ndc_to_screen(self.SIZE_TEXT_X, cy, width, height), _fmt_size(size), "size")
            )
            labels.append(
                LadderLabel(
                    *self._ndc_to_screen(self.BUCKET_TEXT_X, cy, width, height),
                    _fmt_bucket(float(ask_lo[i]), self.bucket),
                    "bucket_ask",
                )
            )

        for i in range(n_bid):
            y1, y0 = self._bid_row_y(i, row_h)
            cy = 0.5 * (y0 + y1)
            depth = float(bid_dp[i])
            size = float(bid_sz[i])
            t = min(depth / max_depth, 1.0)

            self._quad(verts, self.LEFT, y0, self.RIGHT, y1)
            colors.append((self.BID_ROW, 6))

            bar_left = self.DEPTH_RIGHT - bar_span * t
            self._quad(verts, bar_left, y0 + 0.06 * row_h, self.DEPTH_RIGHT, y1 - 0.06 * row_h)
            colors.append((self.BID_BAR, 6))

            labels.append(
                LadderLabel(*self._ndc_to_screen(self.DEPTH_TEXT_X, cy, width, height), _fmt_size(depth), "depth")
            )
            labels.append(
                LadderLabel(*self._ndc_to_screen(self.SIZE_TEXT_X, cy, width, height), _fmt_size(size), "size")
            )
            labels.append(
                LadderLabel(
                    *self._ndc_to_screen(self.BUCKET_TEXT_X, cy, width, height),
                    _fmt_bucket(float(bid_lo[i]), self.bucket),
                    "bucket_bid",
                )
            )

        mid = book.mid()
        bb, ba = book.best_bid(), book.best_ask()
        if mid is not None:
            y0, y1 = -self.MID_GAP * 0.45, self.MID_GAP * 0.45
            self._quad(verts, self.LEFT, y0, self.RIGHT, y1)
            colors.append((self.MID_BG, 6))
            spr = (ba - bb) if bb is not None and ba is not None else 0.0
            mid_text = f"mid {_fmt_mid(mid)}   spr {_fmt_mid(spr)}"
            labels.append(
                LadderLabel(*self._ndc_to_screen(self.BUCKET_TEXT_X, 0.0, width, height), mid_text, "mid")
            )

        # Row separators
        for i in range(n_ask):
            _, y0 = self._ask_row_y(i, row_h)
            self._quad(verts, self.LEFT, y0 - 0.002, self.RIGHT, y0 + 0.002)
            colors.append((self.GRID, 6))
        for i in range(n_bid):
            y1, _ = self._bid_row_y(i, row_h)
            self._quad(verts, self.LEFT, y1 - 0.002, self.RIGHT, y1 + 0.002)
            colors.append((self.GRID, 6))

        data = np.array(verts, dtype=np.float32)
        raw = data.tobytes()
        if len(raw) > self._vbo.size:
            self._vbo.orphan(len(raw))
        self._vbo.write(raw)

        offset = 0
        for color, count in colors:
            self._prog["u_color"].value = color
            self._vao.render(moderngl.TRIANGLES, vertices=count, first=offset)
            offset += count

        self._last_layout = LadderLayout(labels, max_depth)
        return self._last_layout

    def _row_height(self, n_bid: int, n_ask: int) -> float:
        usable = (self.TOP - self.BOTTOM - self.MID_GAP) / max(n_bid + n_ask, 1)
        return usable * 0.98

    def _ask_row_y(self, i: int, row_h: float) -> tuple[float, float]:
        gap = self.MID_GAP * 0.5
        y0 = gap + i * row_h
        return y0 + row_h, y0

    def _bid_row_y(self, i: int, row_h: float) -> tuple[float, float]:
        gap = self.MID_GAP * 0.5
        y1 = -gap - i * row_h
        return y1, y1 - row_h

    @staticmethod
    def _quad(out: list[float], x0: float, y0: float, x1: float, y1: float) -> None:
        out.extend([x0, y0, x1, y0, x1, y1, x0, y0, x1, y1, x0, y1])

    @staticmethod
    def _ndc_to_screen(x: float, y: float, width: int, height: int) -> tuple[float, float]:
        sx = (x * 0.5 + 0.5) * width
        sy = (1.0 - (y * 0.5 + 0.5)) * height
        return sx, sy
