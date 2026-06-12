from __future__ import annotations

from src.market_data.service import MarketDataService
from src.utils.config import TradingConfig
from src.utils.types import MarketSnapshot, OrderBook


class DummyRestClient:
    def __init__(self) -> None:
        self.config = TradingConfig()
        self.snapshot_calls = 0

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        self.snapshot_calls += 1
        return MarketSnapshot(symbol=symbol, last_price=100.0, spread_pct=0.01)

    def get_order_book(self, symbol: str) -> OrderBook:
        return OrderBook(symbol=symbol, best_bid=99.0, best_ask=101.0, bid_size=1.0, ask_size=1.0)


def test_snapshot_uses_websocket_cache_before_rest() -> None:
    rest = DummyRestClient()
    market = MarketDataService(rest)  # type: ignore[arg-type]
    market._handle_ws_event(
        {
            "arg": {"channel": "books5", "instId": "BTC-USDT"},
            "data": [{"bids": [["199.9", "2"]], "asks": [["200.1", "3"]]}],
        }
    )
    market._handle_ws_event(
        {
            "arg": {"channel": "tickers", "instId": "BTC-USDT"},
            "data": [{"last": "200", "volCcy24h": "12345"}],
        }
    )

    snapshot = market.snapshot("BTC-USDT")

    assert snapshot.last_price == 200.0
    assert snapshot.volume_24h == 12345.0
    assert snapshot.spread_pct < 0.002
    assert rest.snapshot_calls == 0
    assert market.realtime_status("BTC-USDT")["source"] == "websocket"
