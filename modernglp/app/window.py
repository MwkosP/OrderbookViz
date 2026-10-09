"""Main application window."""

from __future__ import annotations

import os

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from modernglp.gl.widget import OrderBookGLWidget, ViewMode
from modernglp.orderbook.live import LiveOrderBookFeed
from modernglp.orderbook.model import OrderBook
from modernglp.plots.dom import DomWidget


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        exchange = os.environ.get("MODERNGLP_EXCHANGE", "COINBASE")
        symbol = os.environ.get("MODERNGLP_SYMBOL", "BTC-USD")
        dom_pct = float(os.environ.get("MODERNGLP_DOM_PCT", "1"))

        self.setWindowTitle(f"ModernGLp — {exchange} {symbol}")
        self.resize(1100, 720)

        mono = QFont("IBM Plex Mono", 12)
        if not mono.exactMatch():
            mono = QFont("monospace", 12)

        self._symbol = QLabel(f"{exchange} {symbol}")
        self._mid = QLabel("mid —")
        self._spread = QLabel("spread —")
        for label in (self._symbol, self._mid, self._spread):
            label.setFont(mono)
            label.setStyleSheet("color: #d7dde5; padding: 4px 10px;")
        self._symbol.setStyleSheet("color: #f2f5f8; font-weight: 600; padding: 4px 10px;")

        header = QHBoxLayout()
        header.setContentsMargins(12, 10, 12, 6)
        header.addWidget(self._symbol)
        header.addStretch(1)
        header.addWidget(self._mid)
        header.addWidget(self._spread)

        self._feed = LiveOrderBookFeed(
            exchange=exchange,
            symbol=symbol,
            max_depth=0,
            parent=self,
        )

        # One GL surface shared by L2 + Depth; DOM is a separate pyqtgraph page.
        self.gl = OrderBookGLWidget()
        self.gl.set_mode(ViewMode.L2)
        self.dom = DomWidget(self._feed.stream, mode="pct", val_min=dom_pct)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.gl)   # 0
        self.stack.addWidget(self.dom)  # 1

        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDocumentMode(True)
        self.tabs.addTab("L2 Ladder")
        self.tabs.addTab("Depth")
        self.tabs.addTab("DOM")
        self.tabs.currentChanged.connect(self._on_tab)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.tabs)
        layout.addWidget(self.stack, stretch=1)
        self.setCentralWidget(root)

        self._status = QStatusBar()
        self.setStatusBar(self._status)

        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #121418;
            }
            QTabBar::tab {
                color: #8b93a1;
                background: #1a1e26;
                border: 1px solid #2c3340;
                padding: 6px 16px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                color: #f2f5f8;
                background: #2a3342;
            }
            QStatusBar {
                color: #8b93a1;
                background: #0e1014;
                border-top: 1px solid #242933;
            }
            """
        )

        self._feed.updated.connect(self._on_book)
        self._feed.status.connect(self._status.showMessage)
        self._feed.start()
        self.dom.start()
        self._on_tab(0)

    def _on_tab(self, index: int) -> None:
        if index == 0:
            self.stack.setCurrentWidget(self.gl)
            self.gl.set_mode(ViewMode.L2)
            self._status.showMessage("L2 ladder · ModernGL")
            self.setWindowTitle(f"ModernGLp — L2 · {self._symbol.text()}")
        elif index == 1:
            self.stack.setCurrentWidget(self.gl)
            self.gl.set_mode(ViewMode.DEPTH)
            self._status.showMessage("Depth chart · ModernGL")
            self.setWindowTitle(f"ModernGLp — Depth · {self._symbol.text()}")
        else:
            self.stack.setCurrentWidget(self.dom)
            self._status.showMessage(
                "DOM · size locked at 0 · zoom X expands right · zoom Y for price"
            )
            self.setWindowTitle(self.dom.title_text())

    def _on_book(self, book: OrderBook) -> None:
        self.gl.set_book(book)
        self._symbol.setText(book.symbol)
        mid = book.mid()
        bb, ba = book.best_bid(), book.best_ask()
        if mid is not None:
            decimals = 2 if mid >= 100 else 4 if mid >= 1 else 6
            self._mid.setText(f"mid {mid:.{decimals}f}")
            if bb is not None and ba is not None:
                self._spread.setText(f"spread {ba - bb:.{decimals}f}")

    def closeEvent(self, event) -> None:  # noqa: N802
        self.dom.stop()
        self._feed.stop()
        super().closeEvent(event)
