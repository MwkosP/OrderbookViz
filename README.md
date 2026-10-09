# ModernGLp

Orderbook charting platform using **ModernGL** for GPU drawing, **PyQt6** for the shell, and **cryptofeed** for live exchange data.

## Run

```bash
uv sync
uv run modernglp
```

Default feed is Coinbase `BTC-USD` L2. Override with:

```bash
MODERNGLP_EXCHANGE=BINANCE MODERNGLP_SYMBOL=BTC-USDT uv run modernglp
```

## Layout

```
modernglp/
  data/                  # exchange feeds (cryptofeed)
  app/window.py          # Qt main window
  gl/                    # ModernGL widget + L2/depth renderers
  orderbook/model.py     # in-memory book
  orderbook/live.py      # data → Qt bridge
  orderbook/feed.py      # mock feed (dev)
  main.py
```

## Views

- **L2** — ladder with per-level size bars
- **Depth** — cumulative bid/ask area chart
