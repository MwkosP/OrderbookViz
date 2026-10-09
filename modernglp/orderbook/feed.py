"""Synthetic orderbook feed for local development."""

from __future__ import annotations

import random

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from modernglp.orderbook.model import OrderBook


class MockOrderBookFeed(QObject):
    """Periodically mutates an in-memory book and emits it."""

    updated = pyqtSignal(object)

    def __init__(
        self,
        mid: float = 100.0,
        tick: float = 0.5,
        levels: int = 40,
        interval_ms: int = 50,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.book = OrderBook(tick_size=tick)
        self._mid = mid
        self._tick = tick
        self._levels = levels
        self._rng = random.Random(7)
        self._seed()

        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick_once)

    def start(self) -> None:
        self._timer.start()
        self.updated.emit(self.book)

    def stop(self) -> None:
        self._timer.stop()

    def _seed(self) -> None:
        for i in range(1, self._levels + 1):
            bid_p = self._mid - i * self._tick
            ask_p = self._mid + i * self._tick
            self.book.apply_bid(bid_p, self._size_at(i))
            self.book.apply_ask(ask_p, self._size_at(i))

    def _size_at(self, distance: int) -> float:
        base = 8.0 + distance * 1.4
        return max(0.5, base + self._rng.uniform(-2.0, 4.0))

    def _align(self, price: float) -> float:
        return round(round(price / self._tick) * self._tick, 6)

    def _tick_once(self) -> None:
        # Mild mid drift keeps the book feeling alive.
        self._mid = self._align(self._mid + self._rng.uniform(-0.15, 0.15))

        # Refresh a few random levels near the touch.
        for _ in range(6):
            side = self._rng.choice(("bid", "ask"))
            distance = self._rng.randint(1, self._levels)
            price = self._align(
                (self._mid - distance * self._tick)
                if side == "bid"
                else (self._mid + distance * self._tick)
            )
            size = self._size_at(distance) * self._rng.uniform(0.6, 1.5)
            if side == "bid":
                self.book.apply_bid(price, size)
            else:
                self.book.apply_ask(price, size)

        # Drop stale far levels so the book stays bounded.
        keep_bids = sorted(self.book.bids.keys(), reverse=True)[: self._levels]
        keep_asks = sorted(self.book.asks.keys())[: self._levels]
        self.book.bids = {p: self.book.bids[p] for p in keep_bids}
        self.book.asks = {p: self.book.asks[p] for p in keep_asks}

        self.updated.emit(self.book)
