from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import logging
import threading
import time
from typing import Any

from src.market_data.okx_client import OKXRestClient
from src.market_data.ws_client import OKXPublicWebSocket
from src.utils.types import Candle, MarketSnapshot, OrderBook


class MarketDataService:
    def __init__(self, rest_client: OKXRestClient):
        self.rest_client = rest_client
        self.ws_client = OKXPublicWebSocket(rest_client.config)
        self._lock = threading.Lock()
        self._ticker_cache: dict[str, dict[str, Any]] = {}
        self._book_cache: dict[str, OrderBook] = {}
        self._received_at: dict[str, float] = {}
        self._ws_started = False
        self._ws_stop = threading.Event()
        self._ws_thread: threading.Thread | None = None
        self._ws_error: str | None = None
        self._logger = logging.getLogger(__name__)

    def candles(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        return self.rest_client.get_candles(symbol, timeframe, limit)

    def order_book(self, symbol: str) -> OrderBook:
        with self._lock:
            cached = self._book_cache.get(symbol)
        if cached is not None:
            return cached
        return self.rest_client.get_order_book(symbol)

    def snapshot(self, symbol: str) -> MarketSnapshot:
        cached = self._snapshot_from_cache(symbol)
        if cached is not None:
            return cached
        return self.rest_client.get_snapshot(symbol)

    def start_realtime(self, symbols: list[str], logger: logging.Logger | None = None) -> None:
        if self._ws_started:
            return
        self._logger = logger or self._logger
        self._ws_started = True
        self._ws_stop.clear()
        self._ws_thread = threading.Thread(
            target=self._run_realtime_thread,
            args=(list(symbols),),
            name="okx-public-ws",
            daemon=True,
        )
        self._ws_thread.start()

    def stop_realtime(self) -> None:
        self._ws_stop.set()

    def realtime_status(self, symbol: str | None = None) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            if symbol:
                last_seen = self._received_at.get(symbol)
                has_ticker = symbol in self._ticker_cache
                has_book = symbol in self._book_cache
            else:
                last_seen = max(self._received_at.values(), default=None)
                has_ticker = bool(self._ticker_cache)
                has_book = bool(self._book_cache)
            error = self._ws_error
        age = None if last_seen is None else max(now - last_seen, 0.0)
        connected = age is not None and age < 10
        return {
            "enabled": self._ws_started,
            "connected": connected,
            "source": "websocket" if connected else "rest-fallback",
            "ageSeconds": age,
            "hasTicker": has_ticker,
            "hasOrderBook": has_book,
            "lastError": error,
        }

    async def stream_public_market(self, symbols: list[str]) -> AsyncIterator[dict]:
        channels = []
        for symbol in symbols:
            channels.extend(
                [
                    {"channel": "tickers", "instId": symbol},
                    {"channel": "books5", "instId": symbol},
                    {"channel": "candle1m", "instId": symbol},
                    {"channel": "candle5m", "instId": symbol},
                    {"channel": "candle15m", "instId": symbol},
                ]
        )
        async for event in self.ws_client.subscribe(channels):
            yield event

    def _run_realtime_thread(self, symbols: list[str]) -> None:
        try:
            asyncio.run(self._run_realtime_loop(symbols))
        except Exception as exc:
            with self._lock:
                self._ws_error = str(exc)
            self._logger.warning("OKX websocket stopped: %s", exc)

    async def _run_realtime_loop(self, symbols: list[str]) -> None:
        channels = []
        for symbol in symbols:
            channels.extend(
                [
                    {"channel": "tickers", "instId": symbol},
                    {"channel": "books5", "instId": symbol},
                ]
            )
        while not self._ws_stop.is_set():
            try:
                async for event in self.ws_client.subscribe(channels):
                    if self._ws_stop.is_set():
                        break
                    self._handle_ws_event(event)
            except Exception as exc:
                with self._lock:
                    self._ws_error = str(exc)
                self._logger.warning("OKX websocket reconnecting after error: %s", exc)
                await asyncio.sleep(3)

    def _handle_ws_event(self, event: dict[str, Any]) -> None:
        arg = event.get("arg") or {}
        channel = str(arg.get("channel") or "")
        symbol = str(arg.get("instId") or "")
        rows = event.get("data") or []
        if not symbol or not rows:
            return
        now = time.time()
        row = rows[0]
        with self._lock:
            if channel == "tickers":
                self._ticker_cache[symbol] = row
                self._received_at[symbol] = now
                self._ws_error = None
            elif channel == "books5":
                bids = row.get("bids") or []
                asks = row.get("asks") or []
                if bids and asks:
                    best_bid = bids[0]
                    best_ask = asks[0]
                    self._book_cache[symbol] = OrderBook(
                        symbol=symbol,
                        best_bid=float(best_bid[0]),
                        best_ask=float(best_ask[0]),
                        bid_size=float(best_bid[1]),
                        ask_size=float(best_ask[1]),
                    )
                    self._received_at[symbol] = now
                    self._ws_error = None

    def _snapshot_from_cache(self, symbol: str) -> MarketSnapshot | None:
        with self._lock:
            ticker = dict(self._ticker_cache.get(symbol) or {})
            book = self._book_cache.get(symbol)
            received_at = self._received_at.get(symbol, 0.0)
        if not ticker or time.time() - received_at > 10:
            return None
        spread_pct = 1.0
        if book is not None:
            spread_pct = book.spread_pct
        else:
            bid = _safe_float(ticker.get("bidPx"))
            ask = _safe_float(ticker.get("askPx"))
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
            if mid > 0:
                spread_pct = (ask - bid) / mid
        return MarketSnapshot(
            symbol=symbol,
            last_price=_safe_float(ticker.get("last")),
            spread_pct=spread_pct,
            volume_24h=_safe_float(ticker.get("volCcy24h") or ticker.get("vol24h")),
            funding_rate=None,
        )


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
