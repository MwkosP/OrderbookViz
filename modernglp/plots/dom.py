"""DOM scatter: size (X) vs price (Y) for live L2 levels."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from modernglp.plots.__helpers__ import book_sides, split_symbol


class DomWidget(QWidget):
    """Live DOM scatter embedded as a tab page."""

    def __init__(
        self,
        book,
        *,
        mode: str = "pct",
        val_min: float | None = 1.0,
        val_max: float | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.book = book
        self.mode = mode
        self.val_min = val_min
        self.val_max = val_max
        self.base, self.quote = split_symbol(getattr(book, "symbol", ""))
        self._y_init = False
        self._x_user = False
        self._locking_x = False

        self.plot = pg.PlotWidget()
        self.plot.setBackground("#121418")
        price_label = f"Price ({self.quote})" if self.quote else "Price"
        size_label = f"Size ({self.base})" if self.base else "Size"
        self.plot.setLabel("left", price_label, color="#d7dde5")
        self.plot.setLabel("bottom", size_label, color="#d7dde5")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setMenuEnabled(False)
        self.plot.disableAutoRange()

        self.plot.getAxis("left").setTextPen("#aeb6c2")
        self.plot.getAxis("bottom").setTextPen("#aeb6c2")
        self.plot.getAxis("left").setPen("#2c3340")
        self.plot.getAxis("bottom").setPen("#2c3340")

        vb = self.plot.getViewBox()
        vb.setBackgroundColor("#121418")
        vb.setMouseEnabled(x=True, y=True)
        vb.setMouseMode(vb.RectMode)
        vb.setLimits(xMin=0)
        vb.enableAutoRange(x=False, y=False)
        vb.sigXRangeChanged.connect(self._lock_left)

        self.bid_curve = self.plot.plot(
            pen=None, symbol="s", symbolSize=7,
            symbolBrush=(0x2E, 0xB8, 0x5C), symbolPen=None, name="Bids",
        )
        self.ask_curve = self.plot.plot(
            pen=None, symbol="s", symbolSize=7,
            symbolBrush=(0xE0, 0x4B, 0x4B), symbolPen=None, name="Asks",
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot)

        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.update_plot)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def set_book(self, book) -> None:
        self.book = book
        self.base, self.quote = split_symbol(getattr(book, "symbol", ""))

    def title_text(self) -> str:
        venues = getattr(self.book, "exchanges", None)
        exchange = getattr(self.book, "exchange", "")
        symbol = getattr(self.book, "symbol", "")
        if venues:
            title = f"Live AGG {symbol} DOM [{', '.join(venues)}]"
        else:
            title = f"Live {exchange} {symbol} Order Book"
        if self.mode == "pct" and self.val_min is not None:
            title += f" [Range: ±{self.val_min}%]"
        elif self.mode == "absolute" and self.val_min is not None and self.val_max is not None:
            suffix = f" {self.quote}" if self.quote else ""
            title += f" [Range: {self.val_min} - {self.val_max}{suffix}]"
        return title

    def _lock_left(self, *_):
        """Keep xMin=0; zoom expands/contracts to the right only."""
        if self._locking_x:
            return
        vb = self.plot.getViewBox()
        (x0, x1), _ = vb.viewRange()
        if x0 > 1e-15 or x0 < -1e-15:
            self._locking_x = True
            width = max(x1 - x0, 1e-12)
            vb.setXRange(0.0, width, padding=0)
            self._locking_x = False
            self._x_user = True
        elif abs(x0) <= 1e-15 and self._y_init:
            self._x_user = True

    def update_plot(self) -> None:
        snap = self.book.snapshot()
        bids, asks = book_sides(snap)
        if not bids or not asks:
            return

        best_bid = bids[0][0]
        best_ask = asks[0][0]
        mid = (best_bid + best_ask) / 2.0

        min_p = max_p = None
        if self.mode == "pct" and self.val_min is not None:
            pct = self.val_min / 100.0
            min_p, max_p = mid * (1.0 - pct), mid * (1.0 + pct)
            filtered_bids = [(p, s) for p, s in bids if min_p <= p <= max_p]
            filtered_asks = [(p, s) for p, s in asks if min_p <= p <= max_p]
        elif self.mode == "absolute" and self.val_min is not None and self.val_max is not None:
            min_p, max_p = self.val_min, self.val_max
            filtered_bids = [(p, s) for p, s in bids if min_p <= p <= max_p]
            filtered_asks = [(p, s) for p, s in asks if min_p <= p <= max_p]
        else:
            filtered_bids = list(bids[:30])
            filtered_asks = list(asks[:30])

        bid_prices = np.array([p for p, _ in filtered_bids], dtype=float) if filtered_bids else np.array([])
        bid_sizes = np.array([s for _, s in filtered_bids], dtype=float) if filtered_bids else np.array([])
        ask_prices = np.array([p for p, _ in filtered_asks], dtype=float) if filtered_asks else np.array([])
        ask_sizes = np.array([s for _, s in filtered_asks], dtype=float) if filtered_asks else np.array([])

        self.bid_curve.setData(bid_sizes, bid_prices)
        self.ask_curve.setData(ask_sizes, ask_prices)

        peak = 0.0
        if bid_sizes.size:
            peak = max(peak, float(np.max(bid_sizes)))
        if ask_sizes.size:
            peak = max(peak, float(np.max(ask_sizes)))
        peak = max(peak, 1e-12)

        if not self._x_user:
            self._locking_x = True
            self.plot.setXRange(0.0, peak * 1.08, padding=0)
            self._locking_x = False

        if not self._y_init:
            if min_p is not None and max_p is not None:
                self.plot.setYRange(min_p, max_p, padding=0)
            self._y_init = True
