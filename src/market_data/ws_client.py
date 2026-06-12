from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import websockets

from src.utils.config import TradingConfig
from src.utils.types import TradingMode


class OKXPublicWebSocket:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.url = (
            config.okx_demo_public_ws_url
            if config.trading_mode in (TradingMode.DEMO, TradingMode.PAPER)
            else config.okx_public_ws_url
        )

    async def subscribe(self, channels: list[dict[str, str]]) -> AsyncIterator[dict[str, Any]]:
        async with websockets.connect(self.url, ping_interval=20, ping_timeout=10) as ws:
            await ws.send(json.dumps({"op": "subscribe", "args": channels}))
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=60)
                yield json.loads(msg)

