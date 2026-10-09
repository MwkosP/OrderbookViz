"""In-memory orderbook state."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np


@dataclass
class OrderBook:
    """Price-level book with bids (descending) and asks (ascending)."""

    symbol: str = "MOCK/USD"
    bids: dict[float, float] = field(default_factory=dict)  # price -> size
    asks: dict[float, float] = field(default_factory=dict)
    tick_size: float = 0.5

    def apply_bid(self, price: float, size: float) -> None:
        if size <= 0:
            self.bids.pop(price, None)
        else:
            self.bids[price] = size

    def apply_ask(self, price: float, size: float) -> None:
        if size <= 0:
            self.asks.pop(price, None)
        else:
            self.asks[price] = size

    def best_bid(self) -> float | None:
        return max(self.bids) if self.bids else None

    def best_ask(self) -> float | None:
        return min(self.asks) if self.asks else None

    def mid(self) -> float | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return 0.5 * (bb + ba)

    def level_arrays(
        self, levels: int = 24
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Per-level prices and sizes (not cumulative).

        Bids: best (highest) first. Asks: best (lowest) first.
        """
        bid_levels = sorted(self.bids.items(), key=lambda kv: kv[0], reverse=True)[:levels]
        ask_levels = sorted(self.asks.items(), key=lambda kv: kv[0])[:levels]

        if bid_levels:
            bid_prices = np.array([p for p, _ in bid_levels], dtype=np.float32)
            bid_sizes = np.array([s for _, s in bid_levels], dtype=np.float32)
        else:
            bid_prices = np.empty(0, np.float32)
            bid_sizes = bid_prices

        if ask_levels:
            ask_prices = np.array([p for p, _ in ask_levels], dtype=np.float32)
            ask_sizes = np.array([s for _, s in ask_levels], dtype=np.float32)
        else:
            ask_prices = np.empty(0, np.float32)
            ask_sizes = ask_prices

        return bid_prices, bid_sizes, ask_prices, ask_sizes

    def depth_arrays(self, levels: int = 40) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return bid/ask prices and cumulative sizes for depth charting."""
        bid_prices, bid_sizes, ask_prices, ask_sizes = self.level_arrays(levels)
        bid_cum = np.cumsum(bid_sizes) if bid_sizes.size else bid_sizes
        ask_cum = np.cumsum(ask_sizes) if ask_sizes.size else ask_sizes
        return bid_prices, bid_cum, ask_prices, ask_cum

    def bucket_ladder(
        self, bucket: float, levels: int = 15
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Aggregate levels into price buckets for the L2 ladder.

        Returns
        -------
        ask_lo, ask_size, ask_depth, bid_lo, bid_size, bid_depth
            `*_lo` is the bucket floor. Depth is cumulative size from mid outward.
            Asks: best (nearest mid) first. Bids: best first.
        """
        if bucket <= 0:
            raise ValueError("bucket must be > 0")

        ask_map: dict[float, float] = defaultdict(float)
        for price, size in self.asks.items():
            ask_map[math.floor(price / bucket) * bucket] += size

        bid_map: dict[float, float] = defaultdict(float)
        for price, size in self.bids.items():
            bid_map[math.floor(price / bucket) * bucket] += size

        ba = self.best_ask()
        bb = self.best_bid()

        if ba is not None and ask_map:
            min_lo = math.floor(ba / bucket) * bucket
            ask_keys = sorted(k for k in ask_map if k >= min_lo)[:levels]
            ask_lo = np.array(ask_keys, dtype=np.float64)
            ask_size = np.array([ask_map[k] for k in ask_keys], dtype=np.float64)
            ask_depth = np.cumsum(ask_size)
        else:
            ask_lo = ask_size = ask_depth = np.empty(0, dtype=np.float64)

        if bb is not None and bid_map:
            max_lo = math.floor(bb / bucket) * bucket
            bid_keys = sorted((k for k in bid_map if k <= max_lo), reverse=True)[:levels]
            bid_lo = np.array(bid_keys, dtype=np.float64)
            bid_size = np.array([bid_map[k] for k in bid_keys], dtype=np.float64)
            bid_depth = np.cumsum(bid_size)
        else:
            bid_lo = bid_size = bid_depth = np.empty(0, dtype=np.float64)

        return ask_lo, ask_size, ask_depth, bid_lo, bid_size, bid_depth
