"""Bridge cryptofeed L2 streams into the Qt orderbook model."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from modernglp.data import OrderBookStream, startStreams, streamOrderbook
from modernglp.orderbook.model import OrderBook


class LiveOrderBookFeed(QObject):
    """Polls a cryptofeed L2 stream and emits OrderBook snapshots for the UI."""

    updated = pyqtSignal(object)
    status = pyqtSignal(str)

    def __init__(
        self,
        exchange: str = "COINBASE",
        symbol: str = "BTC-USD",
        *,
        max_depth: int = 0,
        poll_ms: int = 33,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.exchange = exchange
        self.symbol = symbol
        self.max_depth = max_depth
        self.book = OrderBook(symbol=f"{exchange} {symbol}", tick_size=0.01)

        # max_depth=0 keeps the full book so USD buckets stay populated.
        self._stream: OrderBookStream = streamOrderbook(
            exchange, symbol, level="l2", maxDepth=max_depth
        )
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self._poll)
        self._had_data = False

    @property
    def stream(self) -> OrderBookStream:
        """Underlying cryptofeed book stream (snapshot() for DOM / other plots)."""
        return self._stream

    def start(self) -> None:
        startStreams()
        self.status.emit(f"Connecting {self.exchange} {self.symbol}…")
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _poll(self) -> None:
        snap = self._stream.snapshot()
        if not snap.bids and not snap.asks:
            return

        if self.max_depth and self.max_depth > 0:
            bids = {price: size for price, size in snap.bids[: self.max_depth]}
            asks = {price: size for price, size in snap.asks[: self.max_depth]}
        else:
            bids = {price: size for price, size in snap.bids}
            asks = {price: size for price, size in snap.asks}
        self.book.bids = bids
        self.book.asks = asks
        self.book.symbol = f"{self.exchange} {self.symbol}"

        if not self._had_data:
            self._had_data = True
            self.status.emit(f"Live L2 · {self.exchange} {self.symbol}")

        self.updated.emit(self.book)
