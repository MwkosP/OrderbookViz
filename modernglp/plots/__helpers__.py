"""Helpers for plot widgets."""

from __future__ import annotations


def split_symbol(symbol: str) -> tuple[str, str]:
    """Return (base, quote) from 'BTC-USD', 'BTC/USD', or 'COINBASE BTC-USD'."""
    token = symbol.split()[-1]
    for sep in ("-", "/", "_"):
        if sep in token:
            base, quote = token.split(sep, 1)
            return base, quote
    return token, ""


def book_sides(snap) -> tuple[list, list]:
    """Normalize a book snapshot to (bids, asks) as list[(price, size)].

    Supports OrderBookSnapshot, AggregateOrderBookSnapshot (drops exchange tag),
    and objects with .bids/.asks dicts.
    """
    bids_raw = getattr(snap, "bids", None)
    asks_raw = getattr(snap, "asks", None)
    if bids_raw is None and asks_raw is None:
        return [], []

    bids, asks = list(bids_raw or ()), list(asks_raw or ())

    # Aggregate rows: (exchange, price, size)
    if bids and len(bids[0]) == 3:
        bids = [(p, s) for _, p, s in bids]
    if asks and len(asks[0]) == 3:
        asks = [(p, s) for _, p, s in asks]

    # dict price -> size
    if isinstance(bids_raw, dict):
        bids = sorted(((float(p), float(s)) for p, s in bids_raw.items()), reverse=True)
    if isinstance(asks_raw, dict):
        asks = sorted((float(p), float(s)) for p, s in asks_raw.items())

    return bids, asks
