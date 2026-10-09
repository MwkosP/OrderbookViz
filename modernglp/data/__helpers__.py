# modernglp/data/__helpers__.py
"""Internal helpers for the data package. Not part of the public API."""
import math
import threading
import time
from pathlib import Path
from cryptofeed.exchanges import EXCHANGE_MAP
from cryptofeed.defines import L2_BOOK, L3_BOOK

# Repo-root cache for anything we persist (pcaps, dumps, replays) — never top-level data/
_REPO_ROOT = Path(__file__).resolve().parents[2]
RECORDED_DIR = _REPO_ROOT / "cache" / "recorded"

BOOK_LEVELS = {
    "l2": L2_BOOK,
    "l3": L3_BOOK,
    "l2_book": L2_BOOK,
    "l3_book": L3_BOOK,
    2: L2_BOOK,
    3: L3_BOOK,
}

TIMEFRAME_SECONDS = {
    "1s": 1,
    "5s": 5,
    "15s": 15,
    "30s": 30,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

SELL_SIDES = frozenset({"sell", "bid"})

_DEFAULT_SESSION = None
_DEFAULT_LOCK = threading.Lock()


def _runPrintLoop(interval, emit):
    """Sleep, then emit(); repeat. interval<=0 emits once with no sleep."""
    if interval is None or interval <= 0:
        emit()
        return
    while True:
        time.sleep(interval)
        emit()


def _recordedDir(*parts, create=True):
    """Path under cache/recorded/ for saved market data (not modernglp/data/).

    Examples:
        _recordedDir()                          → …/cache/recorded
        _recordedDir("coinbase")                → …/cache/recorded/coinbase/
        _recordedDir("coinbase", "book.json")   → …/cache/recorded/coinbase/book.json
    """
    path = RECORDED_DIR.joinpath(*parts) if parts else RECORDED_DIR
    if create:
        folder = path.parent if path.suffix else path
        folder.mkdir(parents=True, exist_ok=True)
    return path


def _resolveExchange(name):
    """Map a user-facing name like 'Coinbase' to cryptofeed's EXCHANGE_MAP key."""
    lookup = {key.upper(): key for key in EXCHANGE_MAP}
    key = name.strip().upper().replace(" ", "_").replace("-", "_")
    if key not in lookup:
        supported = ", ".join(sorted(EXCHANGE_MAP))
        raise ValueError(f"Unsupported exchange: {name}. Choose from {supported}")
    return lookup[key]


# cryptofeed channel constants for discovery (lazy import avoided at module top for speed)
_CHANNEL_TO_CRYPTOFEED = None


def _cryptofeedChannel(channelKey):
    global _CHANNEL_TO_CRYPTOFEED
    if _CHANNEL_TO_CRYPTOFEED is None:
        from cryptofeed.defines import (
            CANDLES,
            FUNDING,
            L1_BOOK,
            L2_BOOK,
            L3_BOOK,
            LIQUIDATIONS,
            OPEN_INTEREST,
            TICKER,
            TRADES,
        )
        _CHANNEL_TO_CRYPTOFEED = {
            "l1": L1_BOOK,
            "l2": L2_BOOK,
            "l3": L3_BOOK,
            "trades": TRADES,
            "ticker": TICKER,
            "candles": CANDLES,
            "funding": FUNDING,
            "openInterest": OPEN_INTEREST,
            "liquidations": LIQUIDATIONS,
            "footprint": TRADES,  # footprint is trades-derived
        }
    ch = _CHANNEL_TO_CRYPTOFEED.get(channelKey)
    if ch is None:
        raise ValueError(f"No cryptofeed channel for {channelKey!r}")
    return ch


def _exchangesForChannel(channelKey):
    """Return sorted cryptofeed exchange ids that advertise this channel."""
    ch = _cryptofeedChannel(channelKey)
    found = []
    for name, cls in EXCHANGE_MAP.items():
        channels = getattr(cls, "websocket_channels", None) or {}
        if ch in channels:
            found.append(name)
    return sorted(found)


def _resolveExchangeList(exchange, channelKey):
    """Normalize exchange / 'all' / list → list of cryptofeed exchange ids.

    Returns None when `exchange` is a single venue (not aggregate).
    """
    if isinstance(exchange, (list, tuple, set)):
        if not exchange:
            raise ValueError("exchange list is empty")
        return [_resolveExchange(x) for x in exchange]
    if isinstance(exchange, str) and exchange.strip().lower() in (
        "all", "*", "agg", "aggregate", "aggregated",
    ):
        found = _exchangesForChannel(channelKey)
        if not found:
            raise ValueError(f"No exchanges support channel {channelKey!r}")
        return found
    return None


def _fanoutCallbacks(callbacks):
    """Return one async callback that awaits every callback in order."""
    async def _callback(*args, **kwargs):
        for callback in callbacks:
            await callback(*args, **kwargs)
    return _callback


def _defaultSession():
    """Process-wide FeedSession used by stream* helpers."""
    global _DEFAULT_SESSION
    from modernglp.data.feed import FeedSession

    with _DEFAULT_LOCK:
        if _DEFAULT_SESSION is None:
            _DEFAULT_SESSION = FeedSession()
        return _DEFAULT_SESSION


def _parseTimeframe(timeframe):
    """Convert '1m' / '5m' / '1h' into seconds."""
    key = str(timeframe).strip().lower()
    if key not in TIMEFRAME_SECONDS:
        supported = ", ".join(TIMEFRAME_SECONDS)
        raise ValueError(f"Unsupported timeframe: {timeframe!r}. Use one of: {supported}")
    return TIMEFRAME_SECONDS[key]


def _bucketPrice(price, priceBucket):
    """Snap a price to its bucket floor, or return price unchanged."""
    if not priceBucket or priceBucket <= 0:
        return price
    return math.floor(price / priceBucket) * priceBucket


def _resolveBookChannel(level):
    """Map 'l2' / 'l3' / 2 / 3 to a cryptofeed book channel."""
    key = level if not isinstance(level, str) else level.lower()
    channel = BOOK_LEVELS.get(key)
    if channel is None:
        raise ValueError(f"Unsupported book level: {level}. Use 'l2' or 'l3'.")
    return channel
