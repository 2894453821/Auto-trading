from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from src.utils.config import OKXCredentials, TradingConfig
from src.utils.types import Candle, MarketSnapshot, OrderBook, TradingMode


class OKXRestClient:
    def __init__(self, config: TradingConfig, credentials: OKXCredentials):
        self.config = config
        self.credentials = credentials
        self.client = httpx.Client(
            base_url=config.okx_rest_base_url,
            timeout=config.request_timeout_seconds,
        )

    def close(self) -> None:
        self.client.close()

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        if not self.credentials.api_secret:
            raise ValueError("OKX api secret is missing")
        payload = f"{timestamp}{method.upper()}{path}{body}"
        digest = hmac.new(
            self.credentials.api_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode()

    def _headers(self, method: str, path: str, body: str = "", auth: bool = False) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.trading_mode in (TradingMode.DEMO, TradingMode.PAPER):
            headers["x-simulated-trading"] = "1"
        if auth:
            if not self.credentials.complete:
                raise ValueError("Complete OKX credentials are required for private API.")
            timestamp = self._timestamp()
            headers.update(
                {
                    "OK-ACCESS-KEY": self.credentials.api_key or "",
                    "OK-ACCESS-SIGN": self._sign(timestamp, method, path, body),
                    "OK-ACCESS-TIMESTAMP": timestamp,
                    "OK-ACCESS-PASSPHRASE": self.credentials.passphrase or "",
                }
            )
        return headers

    def _get(self, path: str, params: dict[str, Any] | None = None, auth: bool = False) -> Any:
        query = ""
        if params:
            query = "?" + httpx.QueryParams(params).__str__()
        signed_path = f"{path}{query}"
        response = self.client.get(path, params=params, headers=self._headers("GET", signed_path, auth=auth))
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "0":
            raise RuntimeError(f"OKX API error {data.get('code')}: {data.get('msg')}")
        return data.get("data", [])

    def _post(self, path: str, payload: dict[str, Any], auth: bool = True) -> Any:
        body = json.dumps(payload, separators=(",", ":"))
        response = self.client.post(path, content=body, headers=self._headers("POST", path, body, auth=auth))
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "0":
            raise RuntimeError(f"OKX API error {data.get('code')}: {data.get('msg')}")
        return data.get("data", [])

    def get_candles(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        rows = self._get(
            "/api/v5/market/candles",
            {"instId": symbol, "bar": timeframe, "limit": str(limit)},
        )
        candles = [
            Candle(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in rows
        ]
        return sorted(candles, key=lambda c: c.timestamp)

    def get_order_book(self, symbol: str, depth: int = 5) -> OrderBook:
        data = self._get("/api/v5/market/books", {"instId": symbol, "sz": str(depth)})
        if not data:
            raise RuntimeError("empty order book")
        book = data[0]
        best_bid = book["bids"][0]
        best_ask = book["asks"][0]
        return OrderBook(
            symbol=symbol,
            best_bid=float(best_bid[0]),
            best_ask=float(best_ask[0]),
            bid_size=float(best_bid[1]),
            ask_size=float(best_ask[1]),
        )

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        ticker = self._get("/api/v5/market/ticker", {"instId": symbol})[0]
        book = self.get_order_book(symbol)
        funding_rate = None
        if "SWAP" in symbol:
            funding = self._get("/api/v5/public/funding-rate", {"instId": symbol})
            if funding:
                funding_rate = float(funding[0]["fundingRate"])
        return MarketSnapshot(
            symbol=symbol,
            last_price=float(ticker["last"]),
            spread_pct=book.spread_pct,
            volume_24h=float(ticker.get("volCcy24h") or ticker.get("vol24h") or 0),
            funding_rate=funding_rate,
        )

    def get_balance(self) -> Any:
        return self._get("/api/v5/account/balance", auth=True)

    def get_positions(self) -> Any:
        return self._get("/api/v5/account/positions", auth=True)

    def get_open_orders(self, symbol: str | None = None) -> Any:
        params = {"instId": symbol} if symbol else None
        return self._get("/api/v5/trade/orders-pending", params=params, auth=True)

    def place_order(self, payload: dict[str, Any]) -> Any:
        return self._post("/api/v5/trade/order", payload, auth=True)

    def cancel_order(self, symbol: str, order_id: str) -> Any:
        return self._post(
            "/api/v5/trade/cancel-order", {"instId": symbol, "ordId": order_id}, auth=True
        )

    def cancel_all_open_orders(self) -> None:
        for order in self.get_open_orders():
            self.cancel_order(order["instId"], order["ordId"])

