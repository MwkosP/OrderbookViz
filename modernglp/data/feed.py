# modernglp/data/feed.py
"""Feed session, individual stream* helpers, and the stream() dispatcher."""
import math
import threading
from collections import deque
from dataclasses import dataclass, field

from cryptofeed import FeedHandler
from cryptofeed.defines import (
    CANDLES,
    FUNDING,
    L1_BOOK,
    LIQUIDATIONS,
    OPEN_INTEREST,
    TICKER,
    TRADES,
)

from modernglp.data.__helpers__ import (
    _bucketPrice,
    _defaultSession,
    _exchangesForChannel,
    _fanoutCallbacks,
    _parseTimeframe,
    _resolveBookChannel,
    _resolveExchange,
    _resolveExchangeList,
    _runPrintLoop,
    SELL_SIDES,
)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class FeedSession:
    """One FeedHandler shared by every stream. Subscribe first, then start()."""

    def __init__(self):
        self._handler = FeedHandler()
        self._pending = {}
        self._started = False
        self._lock = threading.Lock()

    @property
    def started(self):
        return self._started

    def subscribe(self, exchange, symbol, channel, callback, **feedKwargs):
        """Queue a cryptofeed channel callback for (exchange, symbol). Must run before start()."""
        exchangeId = _resolveExchange(exchange)
        with self._lock:
            if self._started:
                raise RuntimeError(
                    "Cannot subscribe after startStreams(); call all stream* functions first."
                )
            key = (exchangeId, symbol)
            spec = self._pending.setdefault(key, {"channels": {}, "kwargs": {}})
            spec["channels"].setdefault(channel, []).append(callback)
            spec["kwargs"].update(feedKwargs)

    def start(self):
        """Start the background cryptofeed thread. Idempotent. No-op if nothing is subscribed."""
        with self._lock:
            if self._started:
                return
            if not self._pending:
                return
            self._started = True
            pending = dict(self._pending)

        for (exchangeId, symbol), spec in pending.items():
            callbacks = {
                channel: cbs[0] if len(cbs) == 1 else _fanoutCallbacks(cbs)
                for channel, cbs in spec["channels"].items()
            }
            self._handler.add_feed(
                exchangeId,
                symbols=[symbol],
                channels=list(spec["channels"]),
                callbacks=callbacks,
                **spec["kwargs"],
            )
            channels = ", ".join(spec["channels"])
            print(f"Starting {exchangeId} {symbol} ({channels})")

        thread = threading.Thread(
            target=self._handler.run,
            kwargs={"install_signal_handlers": False},
            daemon=True,
        )
        thread.start()


def startStreams(session=None):
    """Start queued market streams in a background thread."""
    (session or _defaultSession()).start()


# ---------------------------------------------------------------------------
# L1
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class L1Snapshot:
    """Top of book: best bid/ask price and size."""
    bidPrice: float
    bidSize: float
    askPrice: float
    askSize: float
    timestamp: float | None = None
    receiptTimestamp: float | None = None


class L1Stream:
    """Latest L1 (BBO) for one exchange/symbol. Plots call snapshot()."""

    def __init__(self, exchange, symbol):
        self.exchange = exchange
        self.symbol = symbol
        self._lock = threading.Lock()
        self._snap = None

    def snapshot(self):
        """Return the latest top-of-book, or None until the first update."""
        with self._lock:
            return self._snap

    def print(self, *, interval=1.0):
        """Sleep `interval`s, print latest L1; repeat. interval<=0 prints once."""
        tag = f"L1 {self.exchange} {self.symbol}"

        def emit():
            snap = self.snapshot()
            if snap is None:
                print(f"{tag}  (waiting…)")
                return
            print(
                f"{tag}  bid={snap.bidPrice} x {snap.bidSize}  "
                f"ask={snap.askPrice} x {snap.askSize}"
            )

        _runPrintLoop(interval, emit)

    async def onL1(self, book, receiptTimestamp):
        snap = L1Snapshot(
            bidPrice=float(book.bid_price),
            bidSize=float(book.bid_size),
            askPrice=float(book.ask_price),
            askSize=float(book.ask_size),
            timestamp=float(book.timestamp) if book.timestamp is not None else None,
            receiptTimestamp=receiptTimestamp,
        )
        with self._lock:
            self._snap = snap


def streamL1(exchange, symbol, *, session=None):
    """Subscribe to L1 / BBO. Returns a stream plots can snapshot(). Does not start I/O."""
    streamObj = L1Stream(exchange, symbol)
    (session or _defaultSession()).subscribe(exchange, symbol, L1_BOOK, streamObj.onL1)
    return streamObj


# ---------------------------------------------------------------------------
# L2 / L3 order book
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """Point-in-time bids/asks as (price, size) tuples. Bids high→low, asks low→high."""
    bids: tuple
    asks: tuple
    receiptTimestamp: float | None = None


class OrderBookStream:
    """Latest L2/L3 book for one exchange/symbol. Plots call snapshot()."""

    def __init__(self, exchange, symbol, level):
        self.exchange = exchange
        self.symbol = symbol
        self.level = level
        self._lock = threading.Lock()
        self._bids = ()
        self._asks = ()
        self._receiptTimestamp = None

    def snapshot(self):
        """Return a copy of the latest bids and asks."""
        with self._lock:
            return OrderBookSnapshot(self._bids, self._asks, self._receiptTimestamp)

    def print(self, *, interval=1.0, levels=5):
        """Sleep `interval`s, print latest book; repeat. interval<=0 prints once."""
        tag = f"{str(self.level).upper()} {self.exchange} {self.symbol}"

        def emit():
            snap = self.snapshot()
            if not snap.bids or not snap.asks:
                print(f"{tag}  (waiting…)")
                return
            print(
                f"{tag}  bid={snap.bids[0]} ask={snap.asks[0]}  "
                f"nBids={len(snap.bids)} nAsks={len(snap.asks)}"
            )
            n = max(0, int(levels))
            if n:
                print("  asks (best→worse):")
                for price, size in snap.asks[:n]:
                    print(f"    {price}  {size}")
                print("  bids (best→worse):")
                for price, size in snap.bids[:n]:
                    print(f"    {price}  {size}")

        _runPrintLoop(interval, emit)

    async def onBook(self, book, receiptTimestamp):
        bids = tuple(sorted(
            ((float(price), float(size)) for price, size in book.book.bids.items()),
            reverse=True,
        ))
        asks = tuple(sorted(
            ((float(price), float(size)) for price, size in book.book.asks.items()),
        ))
        with self._lock:
            self._bids = bids
            self._asks = asks
            self._receiptTimestamp = receiptTimestamp


def streamOrderbook(exchange, symbol, level="l2", *, maxDepth=0, session=None):
    """Subscribe to a live order book. Returns a stream plots can snapshot()."""
    channel = _resolveBookChannel(level)
    streamObj = OrderBookStream(exchange, symbol, level)
    feedKwargs = {}
    if maxDepth:
        feedKwargs["max_depth"] = maxDepth
    (session or _defaultSession()).subscribe(
        exchange, symbol, channel, streamObj.onBook, **feedKwargs
    )
    return streamObj


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

def _tradeSortKey(trade):
    """Oldest → newest: exchange time, then numeric trade id when possible."""
    tid = trade.tradeId
    try:
        tidKey = int(tid) if tid is not None else -1
    except (TypeError, ValueError):
        tidKey = tid or ""
    return (trade.timestamp, tidKey)


@dataclass(frozen=True, slots=True)
class Trade:
    """One public trade."""
    timestamp: float
    price: float
    amount: float
    side: str
    tradeId: str | None
    exchange: str
    symbol: str


class TradeStream:
    """Rolling live tape for one exchange/symbol. Plots call latest()."""

    def __init__(self, exchange, symbol, maxLen):
        self.exchange = exchange
        self.symbol = symbol
        self._lock = threading.Lock()
        self._trades = deque(maxlen=maxLen)
        self._seen = 0
        self._printed = 0

    def latest(self, n=None):
        """Return recent trades, oldest to newest. n=None returns the whole buffer."""
        with self._lock:
            if n is None:
                return list(self._trades)
            return list(self._trades)[-n:]

    def _takeNew(self, n=None):
        """Return new trades oldest→newest and mark them printed (or last n)."""
        with self._lock:
            buf = list(self._trades)
            if n is not None:
                batch = buf[-max(0, int(n)) :]
            else:
                newCount = min(self._seen - self._printed, len(buf))
                batch = buf[-newCount:] if newCount else []
                self._printed = self._seen
        return sorted(batch, key=_tradeSortKey)

    def print(self, *, interval=1.0, n=None):
        """Sleep `interval`s, then print the batch that arrived (oldest→newest).

        Default batch = only unprinted trades. Pass n=… to dump last n instead.
        interval<=0 prints one batch immediately (no loop).
        """
        tag = f"TRADES {self.exchange} {self.symbol}"

        def emit():
            batch = self._takeNew(n)
            if not batch:
                if n is not None or self._seen == 0:
                    print(f"{tag}  (waiting…)")
                return
            for t in batch:
                print(f"{tag}  {t.side}  {t.price}  {t.amount}  id={t.tradeId}")

        _runPrintLoop(interval, emit)

    async def onTrade(self, trade, receiptTimestamp):
        record = Trade(
            timestamp=float(trade.timestamp),
            price=float(trade.price),
            amount=float(trade.amount),
            side=str(trade.side),
            tradeId=str(trade.id) if trade.id is not None else None,
            exchange=self.exchange,
            symbol=self.symbol,
        )
        with self._lock:
            self._trades.append(record)
            self._seen += 1


def streamTrades(exchange, symbol, *, maxLen=10_000, session=None):
    """Subscribe to a live trade tape. Returns a stream plots can read with latest()."""
    streamObj = TradeStream(exchange, symbol, maxLen)
    (session or _defaultSession()).subscribe(exchange, symbol, TRADES, streamObj.onTrade)
    return streamObj


# ---------------------------------------------------------------------------
# Ticker
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TickerSnapshot:
    """Best bid/ask from the ticker channel (sizes often absent)."""
    bid: float
    ask: float
    timestamp: float | None = None
    receiptTimestamp: float | None = None


class TickerStream:
    """Latest ticker for one exchange/symbol. Plots call snapshot()."""

    def __init__(self, exchange, symbol):
        self.exchange = exchange
        self.symbol = symbol
        self._lock = threading.Lock()
        self._snap = None

    def snapshot(self):
        """Return the latest ticker, or None until the first update."""
        with self._lock:
            return self._snap

    def print(self, *, interval=1.0):
        """Sleep `interval`s, print latest ticker; repeat. interval<=0 prints once."""
        tag = f"TICKER {self.exchange} {self.symbol}"

        def emit():
            snap = self.snapshot()
            if snap is None:
                print(f"{tag}  (waiting…)")
                return
            print(f"{tag}  bid={snap.bid}  ask={snap.ask}")

        _runPrintLoop(interval, emit)

    async def onTicker(self, ticker, receiptTimestamp):
        snap = TickerSnapshot(
            bid=float(ticker.bid),
            ask=float(ticker.ask),
            timestamp=float(ticker.timestamp) if ticker.timestamp is not None else None,
            receiptTimestamp=receiptTimestamp,
        )
        with self._lock:
            self._snap = snap


def streamTicker(exchange, symbol, *, session=None):
    """Subscribe to ticker (best bid/ask). Returns a stream plots can snapshot()."""
    streamObj = TickerStream(exchange, symbol)
    (session or _defaultSession()).subscribe(exchange, symbol, TICKER, streamObj.onTicker)
    return streamObj


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Candle:
    """One OHLCV candle."""
    start: float
    stop: float
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int | None
    closed: bool
    timestamp: float | None = None


class CandleStream:
    """Rolling candle history for one exchange/symbol. Plots call latest()."""

    def __init__(self, exchange, symbol, interval, maxLen):
        self.exchange = exchange
        self.symbol = symbol
        self.interval = interval
        self._lock = threading.Lock()
        self._candles = deque(maxlen=maxLen)

    def latest(self, n=None):
        """Return recent candles, oldest to newest. n=None returns the whole buffer."""
        with self._lock:
            if n is None:
                return list(self._candles)
            return list(self._candles)[-n:]

    def print(self, *, interval=1.0, n=None):
        """Sleep `interval`s, print candles oldest→newest; repeat. interval<=0 once."""
        tag = f"CANDLES {self.exchange} {self.symbol} [{self.interval}]"

        def emit():
            candles = self.latest(1 if n is None else n)
            if not candles:
                print(f"{tag}  (waiting…)")
                return
            for c in candles:
                state = "closed" if c.closed else "forming"
                print(
                    f"{tag}  o={c.open} h={c.high} l={c.low} c={c.close} "
                    f"v={c.volume}  {state}"
                )

        _runPrintLoop(interval, emit)

    async def onCandle(self, candle, receiptTimestamp):
        record = Candle(
            start=float(candle.start),
            stop=float(candle.stop),
            interval=str(candle.interval),
            open=float(candle.open),
            high=float(candle.high),
            low=float(candle.low),
            close=float(candle.close),
            volume=float(candle.volume),
            trades=int(candle.trades) if candle.trades is not None else None,
            closed=bool(candle.closed),
            timestamp=float(candle.timestamp) if candle.timestamp is not None else None,
        )
        with self._lock:
            if self._candles and self._candles[-1].start == record.start:
                self._candles[-1] = record
            else:
                self._candles.append(record)


def streamCandles(exchange, symbol, interval="1m", *, maxLen=500, closedOnly=True, session=None):
    """Subscribe to OHLCV candles. Returns a stream plots can read with latest()."""
    streamObj = CandleStream(exchange, symbol, interval, maxLen)
    (session or _defaultSession()).subscribe(
        exchange,
        symbol,
        CANDLES,
        streamObj.onCandle,
        candle_interval=interval,
        candle_closed_only=closedOnly,
    )
    return streamObj


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FundingSnapshot:
    """Latest funding-rate update (perps)."""
    rate: float
    markPrice: float | None
    nextFundingTime: float | None
    predictedRate: float | None
    timestamp: float | None = None
    receiptTimestamp: float | None = None


class FundingStream:
    """Latest funding rate for one exchange/symbol. Plots call snapshot()."""

    def __init__(self, exchange, symbol):
        self.exchange = exchange
        self.symbol = symbol
        self._lock = threading.Lock()
        self._snap = None

    def snapshot(self):
        """Return the latest funding update, or None until the first update."""
        with self._lock:
            return self._snap

    def print(self, *, interval=1.0):
        """Sleep `interval`s, print latest funding; repeat. interval<=0 prints once."""
        tag = f"FUNDING {self.exchange} {self.symbol}"

        def emit():
            snap = self.snapshot()
            if snap is None:
                print(f"{tag}  (waiting…)")
                return
            print(
                f"{tag}  rate={snap.rate}  mark={snap.markPrice}  "
                f"next={snap.nextFundingTime}  predicted={snap.predictedRate}"
            )

        _runPrintLoop(interval, emit)

    async def onFunding(self, funding, receiptTimestamp):
        snap = FundingSnapshot(
            rate=float(funding.rate),
            markPrice=float(funding.mark_price) if funding.mark_price is not None else None,
            nextFundingTime=(
                float(funding.next_funding_time)
                if funding.next_funding_time is not None
                else None
            ),
            predictedRate=(
                float(funding.predicted_rate) if funding.predicted_rate is not None else None
            ),
            timestamp=float(funding.timestamp) if funding.timestamp is not None else None,
            receiptTimestamp=receiptTimestamp,
        )
        with self._lock:
            self._snap = snap


def streamFunding(exchange, symbol, *, session=None):
    """Subscribe to funding rate (perps). Returns a stream plots can snapshot()."""
    streamObj = FundingStream(exchange, symbol)
    (session or _defaultSession()).subscribe(exchange, symbol, FUNDING, streamObj.onFunding)
    return streamObj


# ---------------------------------------------------------------------------
# Open interest
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class OpenInterestSnapshot:
    """Latest open interest (perps/futures)."""
    openInterest: float
    timestamp: float | None = None
    receiptTimestamp: float | None = None


class OpenInterestStream:
    """Latest open interest for one exchange/symbol. Plots call snapshot()."""

    def __init__(self, exchange, symbol):
        self.exchange = exchange
        self.symbol = symbol
        self._lock = threading.Lock()
        self._snap = None

    def snapshot(self):
        """Return the latest open interest, or None until the first update."""
        with self._lock:
            return self._snap

    def print(self, *, interval=1.0):
        """Sleep `interval`s, print latest OI; repeat. interval<=0 prints once."""
        tag = f"OI {self.exchange} {self.symbol}"

        def emit():
            snap = self.snapshot()
            if snap is None:
                print(f"{tag}  (waiting…)")
                return
            print(f"{tag}  openInterest={snap.openInterest}")

        _runPrintLoop(interval, emit)

    async def onOpenInterest(self, oi, receiptTimestamp):
        snap = OpenInterestSnapshot(
            openInterest=float(oi.open_interest),
            timestamp=float(oi.timestamp) if oi.timestamp is not None else None,
            receiptTimestamp=receiptTimestamp,
        )
        with self._lock:
            self._snap = snap


def streamOpenInterest(exchange, symbol, *, session=None):
    """Subscribe to open interest. Returns a stream plots can snapshot()."""
    streamObj = OpenInterestStream(exchange, symbol)
    (session or _defaultSession()).subscribe(
        exchange, symbol, OPEN_INTEREST, streamObj.onOpenInterest
    )
    return streamObj


# ---------------------------------------------------------------------------
# Liquidations
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Liquidation:
    """One forced liquidation event."""
    side: str
    quantity: float
    price: float
    status: str
    liquidationId: str | None
    timestamp: float | None = None
    exchange: str = ""
    symbol: str = ""


class LiquidationStream:
    """Rolling liquidation tape. Plots call latest()."""

    def __init__(self, exchange, symbol, maxLen):
        self.exchange = exchange
        self.symbol = symbol
        self._lock = threading.Lock()
        self._events = deque(maxlen=maxLen)
        self._seen = 0
        self._printed = 0

    def latest(self, n=None):
        """Return recent liquidations, oldest to newest. n=None returns the whole buffer."""
        with self._lock:
            if n is None:
                return list(self._events)
            return list(self._events)[-n:]

    def _takeNew(self, n=None):
        """Return new liquidations oldest→newest and mark them printed (or last n)."""
        with self._lock:
            buf = list(self._events)
            if n is not None:
                batch = buf[-max(0, int(n)) :]
            else:
                newCount = min(self._seen - self._printed, len(buf))
                batch = buf[-newCount:] if newCount else []
                self._printed = self._seen
        return sorted(
            batch,
            key=lambda e: (
                e.timestamp or 0.0,
                int(e.liquidationId)
                if e.liquidationId is not None and str(e.liquidationId).isdigit()
                else (e.liquidationId or ""),
            ),
        )

    def print(self, *, interval=1.0, n=None):
        """Sleep `interval`s, then print new liqs oldest→newest; repeat.

        interval<=0 prints one batch immediately.
        """
        tag = f"LIQS {self.exchange} {self.symbol}"

        def emit():
            batch = self._takeNew(n)
            if not batch:
                if n is not None or self._seen == 0:
                    print(f"{tag}  (waiting…)")
                return
            for e in batch:
                print(
                    f"{tag}  {e.side}  qty={e.quantity}  px={e.price}  "
                    f"status={e.status}  id={e.liquidationId}"
                )

        _runPrintLoop(interval, emit)

    async def onLiquidation(self, event, receiptTimestamp):
        record = Liquidation(
            side=str(event.side),
            quantity=float(event.quantity),
            price=float(event.price),
            status=str(event.status),
            liquidationId=str(event.id) if event.id is not None else None,
            timestamp=float(event.timestamp) if event.timestamp is not None else None,
            exchange=self.exchange,
            symbol=self.symbol,
        )
        with self._lock:
            self._events.append(record)
            self._seen += 1


def streamLiquidations(exchange, symbol, *, maxLen=5_000, session=None):
    """Subscribe to liquidations. Returns a stream plots can read with latest()."""
    streamObj = LiquidationStream(exchange, symbol, maxLen)
    (session or _defaultSession()).subscribe(
        exchange, symbol, LIQUIDATIONS, streamObj.onLiquidation
    )
    return streamObj


# ---------------------------------------------------------------------------
# Footprint (trades → OHLC + bid/ask volume at price)
# ---------------------------------------------------------------------------

@dataclass
class FootprintBar:
    """One candle + footprint levels. levels are (price, bidVol, askVol), high→low."""
    start: float
    end: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    levels: tuple = ()
    closed: bool = False

    @property
    def delta(self):
        """Ask (buy aggression) minus bid (sell aggression) across the bar."""
        return sum(ask - bid for _, bid, ask in self.levels)


@dataclass
class _FormingBar:
    """In-progress bar used only while aggregating trades."""
    start: float
    end: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    cells: dict = field(default_factory=dict)

    def toBar(self, *, closed):
        levels = tuple(
            (price, vols[0], vols[1])
            for price, vols in sorted(self.cells.items(), reverse=True)
        )
        return FootprintBar(
            start=self.start,
            end=self.end,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            levels=levels,
            closed=closed,
        )


class FootprintStream:
    """Live footprint bars for one exchange/symbol/timeframe. Plots call bars()."""

    def __init__(self, exchange, symbol, timeframe, priceBucket, maxBars):
        self.exchange = exchange
        self.symbol = symbol
        self.timeframe = timeframe
        self.interval = _parseTimeframe(timeframe)
        self.priceBucket = priceBucket
        self.maxBars = maxBars
        self._lock = threading.Lock()
        self._closed = deque(maxlen=maxBars)
        self._forming: _FormingBar | None = None

    def bars(self, n=None):
        """Return footprint bars oldest→newest (includes the forming bar)."""
        with self._lock:
            closed = list(self._closed)
            forming = None if self._forming is None else self._forming.toBar(closed=False)
        out = closed + ([forming] if forming is not None else [])
        if n is None:
            return out
        return out[-n:]

    def print(self, *, interval=1.0, n=None, levels=5):
        """Sleep `interval`s, print bars oldest→newest; repeat. interval<=0 once."""
        tag = f"FOOTPRINT {self.exchange} {self.symbol} [{self.timeframe}]"

        def emit():
            bars = self.bars(1 if n is None else n)
            if not bars:
                print(f"{tag}  (waiting…)")
                return
            for bar in bars:
                state = "closed" if bar.closed else "forming"
                print(
                    f"{tag}  o={bar.open} h={bar.high} l={bar.low} c={bar.close} "
                    f"v={bar.volume} Δ={bar.delta}  {state}"
                )
                for price, bidVol, askVol in bar.levels[: max(0, int(levels))]:
                    print(f"    {price}  bid={bidVol}  ask={askVol}")

        _runPrintLoop(interval, emit)

    def _barStart(self, ts):
        return math.floor(ts / self.interval) * self.interval

    def _rollTo(self, start):
        if self._forming is not None:
            self._closed.append(self._forming.toBar(closed=True))
        self._forming = None

    async def onTrade(self, trade, receiptTimestamp):
        ts = float(trade.timestamp)
        price = float(trade.price)
        amount = float(trade.amount)
        side = str(trade.side).lower()
        start = self._barStart(ts)
        bucket = _bucketPrice(price, self.priceBucket)

        with self._lock:
            if self._forming is not None and start > self._forming.start:
                self._rollTo(start)
            elif self._forming is not None and start < self._forming.start:
                return

            if self._forming is None:
                self._forming = _FormingBar(
                    start=start,
                    end=start + self.interval,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                )

            bar = self._forming
            bar.high = max(bar.high, price)
            bar.low = min(bar.low, price)
            bar.close = price
            bar.volume += amount

            cell = bar.cells.setdefault(bucket, [0.0, 0.0])
            if side in SELL_SIDES:
                cell[0] += amount
            else:
                cell[1] += amount


def streamFootprint(
    exchange,
    symbol,
    timeframe="1m",
    *,
    priceBucket=None,
    maxBars=200,
    session=None,
):
    """Subscribe to trades and aggregate footprint bars. Does not start I/O."""
    streamObj = FootprintStream(exchange, symbol, timeframe, priceBucket, maxBars)
    (session or _defaultSession()).subscribe(exchange, symbol, TRADES, streamObj.onTrade)
    return streamObj


# ---------------------------------------------------------------------------
# Multi-exchange aggregates (total picture across venues)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AggregateOrderBookSnapshot:
    """Merged L2/L3 view. Levels are (exchange, price, size)."""
    bids: tuple  # high→low by price
    asks: tuple  # low→high by price
    byExchange: dict  # exchange → OrderBookSnapshot


@dataclass(frozen=True, slots=True)
class AggregateL1Snapshot:
    """Per-venue L1 plus NBBO across venues."""
    venues: tuple  # (exchange, L1Snapshot)
    bidPrice: float | None
    bidSize: float | None
    bidExchange: str | None
    askPrice: float | None
    askSize: float | None
    askExchange: str | None


@dataclass(frozen=True, slots=True)
class AggregateTickerSnapshot:
    """Per-venue tickers plus best bid/ask across venues."""
    venues: tuple  # (exchange, TickerSnapshot)
    bid: float | None
    ask: float | None
    bidExchange: str | None
    askExchange: str | None


class AggregateOrderBookStream:
    """Merged order books from several exchanges. Plots call snapshot()."""

    def __init__(self, symbol, level, children):
        self.symbol = symbol
        self.level = level
        self.exchange = "AGG"
        self.children = list(children)
        self.exchanges = tuple(c.exchange for c in self.children)

    def snapshot(self):
        """Merged levels as (exchange, price, size) plus per-venue snapshots."""
        bids, asks = [], []
        byExchange = {}
        for child in self.children:
            snap = child.snapshot()
            byExchange[child.exchange] = snap
            for price, size in snap.bids:
                bids.append((child.exchange, price, size))
            for price, size in snap.asks:
                asks.append((child.exchange, price, size))
        bids.sort(key=lambda row: (-row[1], row[0]))
        asks.sort(key=lambda row: (row[1], row[0]))
        return AggregateOrderBookSnapshot(
            bids=tuple(bids), asks=tuple(asks), byExchange=byExchange
        )

    def print(self, *, interval=1.0, levels=5):
        """Sleep, print merged BBO + top levels tagged by exchange; repeat."""
        tag = f"AGG {str(self.level).upper()} {self.symbol} [{','.join(self.exchanges)}]"

        def emit():
            snap = self.snapshot()
            if not snap.bids or not snap.asks:
                print(f"{tag}  (waiting…)")
                return
            print(
                f"{tag}  bid={snap.bids[0]} ask={snap.asks[0]}  "
                f"nBids={len(snap.bids)} nAsks={len(snap.asks)}"
            )
            n = max(0, int(levels))
            if n:
                print("  asks (best→worse):")
                for ex, price, size in snap.asks[:n]:
                    print(f"    [{ex}]  {price}  {size}")
                print("  bids (best→worse):")
                for ex, price, size in snap.bids[:n]:
                    print(f"    [{ex}]  {price}  {size}")

        _runPrintLoop(interval, emit)


class AggregateL1Stream:
    """L1 from several exchanges + NBBO."""

    def __init__(self, symbol, children):
        self.symbol = symbol
        self.exchange = "AGG"
        self.children = list(children)
        self.exchanges = tuple(c.exchange for c in self.children)

    def snapshot(self):
        venues = []
        bestBid = bestAsk = None
        for child in self.children:
            snap = child.snapshot()
            if snap is None:
                continue
            venues.append((child.exchange, snap))
            if bestBid is None or snap.bidPrice > bestBid[0]:
                bestBid = (snap.bidPrice, snap.bidSize, child.exchange)
            if bestAsk is None or snap.askPrice < bestAsk[0]:
                bestAsk = (snap.askPrice, snap.askSize, child.exchange)
        return AggregateL1Snapshot(
            venues=tuple(venues),
            bidPrice=None if bestBid is None else bestBid[0],
            bidSize=None if bestBid is None else bestBid[1],
            bidExchange=None if bestBid is None else bestBid[2],
            askPrice=None if bestAsk is None else bestAsk[0],
            askSize=None if bestAsk is None else bestAsk[1],
            askExchange=None if bestAsk is None else bestAsk[2],
        )

    def print(self, *, interval=1.0):
        tag = f"AGG L1 {self.symbol} [{','.join(self.exchanges)}]"

        def emit():
            snap = self.snapshot()
            if not snap.venues:
                print(f"{tag}  (waiting…)")
                return
            print(
                f"{tag}  NBBO bid={snap.bidPrice}@{snap.bidExchange}  "
                f"ask={snap.askPrice}@{snap.askExchange}"
            )
            for ex, v in snap.venues:
                print(
                    f"  [{ex}]  bid={v.bidPrice} x {v.bidSize}  "
                    f"ask={v.askPrice} x {v.askSize}"
                )

        _runPrintLoop(interval, emit)


class AggregateTickerStream:
    """Tickers from several exchanges."""

    def __init__(self, symbol, children):
        self.symbol = symbol
        self.exchange = "AGG"
        self.children = list(children)
        self.exchanges = tuple(c.exchange for c in self.children)

    def snapshot(self):
        venues = []
        bestBid = bestAsk = None
        for child in self.children:
            snap = child.snapshot()
            if snap is None:
                continue
            venues.append((child.exchange, snap))
            if bestBid is None or snap.bid > bestBid[0]:
                bestBid = (snap.bid, child.exchange)
            if bestAsk is None or snap.ask < bestAsk[0]:
                bestAsk = (snap.ask, child.exchange)
        return AggregateTickerSnapshot(
            venues=tuple(venues),
            bid=None if bestBid is None else bestBid[0],
            ask=None if bestAsk is None else bestAsk[0],
            bidExchange=None if bestBid is None else bestBid[1],
            askExchange=None if bestAsk is None else bestAsk[1],
        )

    def print(self, *, interval=1.0):
        tag = f"AGG TICKER {self.symbol} [{','.join(self.exchanges)}]"

        def emit():
            snap = self.snapshot()
            if not snap.venues:
                print(f"{tag}  (waiting…)")
                return
            print(
                f"{tag}  best bid={snap.bid}@{snap.bidExchange}  "
                f"ask={snap.ask}@{snap.askExchange}"
            )
            for ex, v in snap.venues:
                print(f"  [{ex}]  bid={v.bid}  ask={v.ask}")

        _runPrintLoop(interval, emit)


class AggregateTradeStream:
    """Merged trade tape from several exchanges."""

    def __init__(self, symbol, children):
        self.symbol = symbol
        self.exchange = "AGG"
        self.children = list(children)
        self.exchanges = tuple(c.exchange for c in self.children)

    def latest(self, n=None):
        """All child trades merged, oldest→newest."""
        merged = []
        for child in self.children:
            merged.extend(child.latest())
        merged.sort(key=_tradeSortKey)
        if n is None:
            return merged
        return merged[-n:]

    def print(self, *, interval=1.0, n=None):
        """Sleep, print new trades from all venues oldest→newest (with exchange)."""
        tag = f"AGG TRADES {self.symbol} [{','.join(self.exchanges)}]"

        def emit():
            batch = []
            for child in self.children:
                batch.extend(child._takeNew(n if n is not None else None))
            # when n is set, _takeNew dumps last n per child — re-sort global
            if n is not None:
                batch = sorted(batch, key=_tradeSortKey)[-max(0, int(n)) :]
            else:
                batch = sorted(batch, key=_tradeSortKey)
            if not batch:
                if n is not None or all(c._seen == 0 for c in self.children):
                    print(f"{tag}  (waiting…)")
                return
            for t in batch:
                print(
                    f"TRADES [{t.exchange}] {t.symbol}  {t.side}  "
                    f"{t.price}  {t.amount}  id={t.tradeId}"
                )

        _runPrintLoop(interval, emit)


class AggregateLiquidationStream:
    """Merged liquidations from several exchanges."""

    def __init__(self, symbol, children):
        self.symbol = symbol
        self.exchange = "AGG"
        self.children = list(children)
        self.exchanges = tuple(c.exchange for c in self.children)

    def latest(self, n=None):
        merged = []
        for child in self.children:
            merged.extend(child.latest())
        merged.sort(
            key=lambda e: (
                e.timestamp or 0.0,
                e.liquidationId or "",
            )
        )
        if n is None:
            return merged
        return merged[-n:]

    def print(self, *, interval=1.0, n=None):
        tag = f"AGG LIQS {self.symbol} [{','.join(self.exchanges)}]"

        def emit():
            batch = []
            for child in self.children:
                batch.extend(child._takeNew(n if n is not None else None))
            if n is not None:
                batch = sorted(
                    batch,
                    key=lambda e: (e.timestamp or 0.0, e.liquidationId or ""),
                )[-max(0, int(n)) :]
            else:
                batch = sorted(
                    batch,
                    key=lambda e: (e.timestamp or 0.0, e.liquidationId or ""),
                )
            if not batch:
                if n is not None or all(c._seen == 0 for c in self.children):
                    print(f"{tag}  (waiting…)")
                return
            for e in batch:
                print(
                    f"LIQS [{e.exchange}] {e.symbol}  {e.side}  "
                    f"qty={e.quantity}  px={e.price}  status={e.status}"
                )

        _runPrintLoop(interval, emit)


class AggregateCandleStream:
    """Candles from several exchanges (side-by-side, not merged OHLC)."""

    def __init__(self, symbol, interval, children):
        self.symbol = symbol
        self.interval = interval
        self.exchange = "AGG"
        self.children = list(children)
        self.exchanges = tuple(c.exchange for c in self.children)

    def latest(self, n=None):
        """Dict exchange → candle list."""
        return {c.exchange: c.latest(n) for c in self.children}

    def print(self, *, interval=1.0, n=None):
        tag = f"AGG CANDLES {self.symbol} [{self.interval}]"

        def emit():
            anyData = False
            for child in self.children:
                candles = child.latest(1 if n is None else n)
                if not candles:
                    continue
                anyData = True
                for c in candles:
                    state = "closed" if c.closed else "forming"
                    print(
                        f"CANDLES [{child.exchange}] {self.symbol}  "
                        f"o={c.open} h={c.high} l={c.low} c={c.close} "
                        f"v={c.volume}  {state}"
                    )
            if not anyData:
                print(f"{tag}  (waiting…)")

        _runPrintLoop(interval, emit)


class AggregateFundingStream:
    def __init__(self, symbol, children):
        self.symbol = symbol
        self.exchange = "AGG"
        self.children = list(children)
        self.exchanges = tuple(c.exchange for c in self.children)

    def snapshot(self):
        return {
            c.exchange: c.snapshot()
            for c in self.children
            if c.snapshot() is not None
        }

    def print(self, *, interval=1.0):
        tag = f"AGG FUNDING {self.symbol}"

        def emit():
            rows = self.snapshot()
            if not rows:
                print(f"{tag}  (waiting…)")
                return
            for ex, snap in rows.items():
                print(
                    f"FUNDING [{ex}] {self.symbol}  rate={snap.rate}  "
                    f"mark={snap.markPrice}"
                )

        _runPrintLoop(interval, emit)


class AggregateOpenInterestStream:
    def __init__(self, symbol, children):
        self.symbol = symbol
        self.exchange = "AGG"
        self.children = list(children)
        self.exchanges = tuple(c.exchange for c in self.children)

    def snapshot(self):
        return {
            c.exchange: c.snapshot()
            for c in self.children
            if c.snapshot() is not None
        }

    def print(self, *, interval=1.0):
        tag = f"AGG OI {self.symbol}"

        def emit():
            rows = self.snapshot()
            if not rows:
                print(f"{tag}  (waiting…)")
                return
            for ex, snap in rows.items():
                print(f"OI [{ex}] {self.symbol}  openInterest={snap.openInterest}")

        _runPrintLoop(interval, emit)


class AggregateFootprintStream:
    """Footprints per exchange (not cross-merged)."""

    def __init__(self, symbol, timeframe, children):
        self.symbol = symbol
        self.timeframe = timeframe
        self.exchange = "AGG"
        self.children = list(children)
        self.exchanges = tuple(c.exchange for c in self.children)

    def bars(self, n=None):
        return {c.exchange: c.bars(n) for c in self.children}

    def print(self, *, interval=1.0, n=None, levels=5):
        tag = f"AGG FOOTPRINT {self.symbol} [{self.timeframe}]"

        def emit():
            anyData = False
            for child in self.children:
                bars = child.bars(1 if n is None else n)
                if not bars:
                    continue
                anyData = True
                for bar in bars:
                    state = "closed" if bar.closed else "forming"
                    print(
                        f"FOOTPRINT [{child.exchange}] {self.symbol}  "
                        f"o={bar.open} h={bar.high} l={bar.low} c={bar.close} "
                        f"v={bar.volume} Δ={bar.delta}  {state}"
                    )
                    for price, bidVol, askVol in bar.levels[: max(0, int(levels))]:
                        print(f"    {price}  bid={bidVol}  ask={askVol}")
            if not anyData:
                print(f"{tag}  (waiting…)")

        _runPrintLoop(interval, emit)


def _symbolForExchange(exchangeId, symbol, symbolMap):
    if not symbolMap:
        return symbol
    return symbolMap.get(exchangeId, symbolMap.get(exchangeId.lower(), symbol))


def _openChildren(exchanges, symbol, channelKey, session, options, symbolMap):
    """Create per-exchange stream* children for an aggregate."""
    children = []
    for ex in exchanges:
        sym = _symbolForExchange(ex, symbol, symbolMap)
        if channelKey == "l1":
            children.append(streamL1(ex, sym, session=session))
        elif channelKey in ("l2", "l3"):
            children.append(
                streamOrderbook(
                    ex,
                    sym,
                    level=channelKey,
                    maxDepth=options.get("maxDepth", 0),
                    session=session,
                )
            )
        elif channelKey == "trades":
            children.append(
                streamTrades(
                    ex, sym, maxLen=options.get("maxLen", 10_000), session=session
                )
            )
        elif channelKey == "ticker":
            children.append(streamTicker(ex, sym, session=session))
        elif channelKey == "candles":
            interval = options.get(
                "candleInterval", options.get("interval", "1m")
            )
            children.append(
                streamCandles(
                    ex,
                    sym,
                    interval=interval,
                    maxLen=options.get("maxLen", 500),
                    closedOnly=options.get("closedOnly", True),
                    session=session,
                )
            )
        elif channelKey == "funding":
            children.append(streamFunding(ex, sym, session=session))
        elif channelKey == "openInterest":
            children.append(streamOpenInterest(ex, sym, session=session))
        elif channelKey == "liquidations":
            children.append(
                streamLiquidations(
                    ex, sym, maxLen=options.get("maxLen", 5_000), session=session
                )
            )
        elif channelKey == "footprint":
            children.append(
                streamFootprint(
                    ex,
                    sym,
                    timeframe=options.get("timeframe", "1m"),
                    priceBucket=options.get("priceBucket", None),
                    maxBars=options.get("maxBars", 200),
                    session=session,
                )
            )
    return children


# ---------------------------------------------------------------------------
# Unified dispatcher
# ---------------------------------------------------------------------------

_CHANNEL_ALIASES = {
    "l1": "l1",
    "l1_book": "l1",
    "l2": "l2",
    "l2_book": "l2",
    "l3": "l3",
    "l3_book": "l3",
    "book": "l2",
    "orderbook": "l2",
    "trades": "trades",
    "trade": "trades",
    "ticker": "ticker",
    "candles": "candles",
    "candle": "candles",
    "funding": "funding",
    "open_interest": "openInterest",
    "openinterest": "openInterest",
    "oi": "openInterest",
    "liquidations": "liquidations",
    "liquidation": "liquidations",
    "footprint": "footprint",
}


def stream(exchange, symbol, channel, *, session=None, **options):
    """Subscribe to one channel on one or many exchanges.

    Single venue:
        book = stream("Coinbase", "BTC-USD", "l2")
        tape = stream("Coinbase", "BTC-USD", "trades")

    Aggregated (all venues that advertise the channel, or an explicit list):
        book = stream("all", "BTC-USD", "l2")
        tape = stream(["Coinbase", "Kraken", "Bitfinex"], "BTC-USD", "trades")
        # optional per-venue symbols:
        stream("all", "BTC-USD", "l2", symbolMap={"BINANCE": "BTC-USDT"})

    Channel: l1 | l2 | l3 | trades | ticker | candles | funding |
             openInterest | liquidations | footprint
    """
    key = _CHANNEL_ALIASES.get(str(channel).strip().lower())
    if key is None:
        supported = ", ".join(sorted(set(_CHANNEL_ALIASES.values())))
        raise ValueError(f"Unknown channel {channel!r}. Use one of: {supported}")

    symbolMap = options.pop("symbolMap", None)
    exchangeIds = _resolveExchangeList(exchange, key)

    if exchangeIds is not None:
        # multi-exchange aggregate
        childOpts = dict(options)
        children = _openChildren(
            exchangeIds, symbol, key, session, childOpts, symbolMap
        )
        # consume known kwargs so leftover check works
        for name in (
            "maxDepth", "maxLen", "candleInterval", "interval", "closedOnly",
            "timeframe", "priceBucket", "maxBars",
        ):
            options.pop(name, None)
        if not children:
            raise ValueError(f"No streams opened for aggregate {key!r}")
        print(
            f"Aggregate {key} on {symbol}: "
            + ", ".join(c.exchange for c in children)
        )
        if key == "l1":
            result = AggregateL1Stream(symbol, children)
        elif key in ("l2", "l3"):
            result = AggregateOrderBookStream(symbol, key, children)
        elif key == "trades":
            result = AggregateTradeStream(symbol, children)
        elif key == "ticker":
            result = AggregateTickerStream(symbol, children)
        elif key == "candles":
            result = AggregateCandleStream(
                symbol, children[0].interval, children
            )
        elif key == "funding":
            result = AggregateFundingStream(symbol, children)
        elif key == "openInterest":
            result = AggregateOpenInterestStream(symbol, children)
        elif key == "liquidations":
            result = AggregateLiquidationStream(symbol, children)
        elif key == "footprint":
            result = AggregateFootprintStream(
                symbol, children[0].timeframe, children
            )
        else:
            raise ValueError(f"Unhandled aggregate channel {key!r}")
        if options:
            raise TypeError(
                f"stream(..., {channel!r}) got unexpected options: {sorted(options)}"
            )
        return result

    # single exchange
    if key == "l1":
        result = streamL1(exchange, symbol, session=session)
    elif key in ("l2", "l3"):
        result = streamOrderbook(
            exchange,
            symbol,
            level=key,
            maxDepth=options.pop("maxDepth", 0),
            session=session,
        )
    elif key == "trades":
        result = streamTrades(
            exchange, symbol, maxLen=options.pop("maxLen", 10_000), session=session
        )
    elif key == "ticker":
        result = streamTicker(exchange, symbol, session=session)
    elif key == "candles":
        interval = options.pop("candleInterval", options.pop("interval", "1m"))
        result = streamCandles(
            exchange,
            symbol,
            interval=interval,
            maxLen=options.pop("maxLen", 500),
            closedOnly=options.pop("closedOnly", True),
            session=session,
        )
    elif key == "funding":
        result = streamFunding(exchange, symbol, session=session)
    elif key == "openInterest":
        result = streamOpenInterest(exchange, symbol, session=session)
    elif key == "liquidations":
        result = streamLiquidations(
            exchange, symbol, maxLen=options.pop("maxLen", 5_000), session=session
        )
    elif key == "footprint":
        result = streamFootprint(
            exchange,
            symbol,
            timeframe=options.pop("timeframe", "1m"),
            priceBucket=options.pop("priceBucket", None),
            maxBars=options.pop("maxBars", 200),
            session=session,
        )
    else:
        raise ValueError(f"Unhandled channel {key!r}")  # pragma: no cover

    if options:
        raise TypeError(f"stream(..., {channel!r}) got unexpected options: {sorted(options)}")
    return result

