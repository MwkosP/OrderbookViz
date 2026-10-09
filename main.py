"""Dev entrypoint — prefer `uv run modernglp` once installed."""

from modernglp.main import main

if __name__ == "__main__":
    raise SystemExit(main())
