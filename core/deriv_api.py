import asyncio
import json
import urllib.request
from typing import Any, Callable, Optional

import websockets

from core.logger import get_logger

logger = get_logger("deriv-api")


class DerivAPI:
    """Async WebSocket client for the Deriv API.

    Supports two authentication modes:
    - Legacy: wss://ws.derivws.com/websockets/v3 + authorize token (deprecated)
    - New API: REST OTP flow → wss://api.derivws.com/trading/v1/options/ws/{demo|real}?otp=...

    The new API uses Personal Access Tokens (PAT) with OAuth-style headers
    to obtain a one-time WebSocket URL via REST, then connects to that URL.
    """

    def __init__(self, ws_url: str, api_token: str, app_id: str = None, pat_token: str = None, account_id: str = None):
        """Initialize Deriv API client.

        Args:
            ws_url: WebSocket URL (legacy) or unused with PAT mode
            api_token: Legacy API token (for authorize flow)
            app_id: Deriv app ID (for new PAT flow)
            pat_token: Personal Access Token (for new PAT flow)
            account_id: Account ID to connect to (for new PAT flow)
        """
        self.ws_url = ws_url
        self.api_token = api_token
        self.app_id = app_id
        self.pat_token = pat_token
        self.account_id = account_id
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: dict[str, Callable] = {}
        self._listen_task: Optional[asyncio.Task] = None
        self._response_timeout = 60.0
        self._use_pat = pat_token is not None

    @staticmethod
    def _rest_request(url: str, method: str = "GET", headers: dict = None, body: dict = None) -> dict:
        """Make a REST API request and return parsed JSON response."""
        if headers is None:
            headers = {}
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, method=method, headers=headers, data=data)
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            logger.error(f"REST {method} {url} → HTTP {e.code}: {error_body[:500]}")
            raise RuntimeError(f"REST API error {e.code}: {error_body[:200]}") from e

    @staticmethod
    def get_otp_ws_url(pat_token: str, app_id: str, account_id: str) -> str:
        """Get authenticated WebSocket URL via REST OTP flow.

        This is the new Deriv API authentication:
        1. REST call to get OTP
        2. Returns WebSocket URL with OTP embedded
        """
        headers = {
            'Authorization': f'Bearer {pat_token}',
            'Deriv-App-ID': app_id,
            'Content-Type': 'application/json',
        }
        otp_url = f'https://api.derivws.com/trading/v1/options/accounts/{account_id}/otp'
        result = DerivAPI._rest_request(otp_url, method="POST", headers=headers)

        if 'data' not in result or 'url' not in result['data']:
            raise RuntimeError(f"OTP response missing URL: {json.dumps(result)[:300]}")

        return result['data']['url']

    async def connect(self):
        """Connect to Deriv WebSocket.

        For PAT mode: obtains OTP URL via REST then connects.
        For legacy mode: connects directly to ws_url.
        """
        if self._use_pat:
            logger.info(f"Connecting via PAT flow (app_id={self.app_id}, account={self.account_id})...")
            self.ws_url = self.get_otp_ws_url(self.pat_token, self.app_id, self.account_id)
            logger.info(f"OTP WebSocket URL obtained")

        logger.info(f"Connecting to {self.ws_url[:80]}...")
        self._ws = await websockets.connect(
            self.ws_url,
            ping_interval=30,
            ping_timeout=25,
            user_agent_header="bloc-trade/1.0",
            close_timeout=10,
        )
        await asyncio.sleep(0.1)
        self._listen_task = asyncio.create_task(self._listen())
        logger.info("WebSocket connected")

    async def _listen(self):
        try:
            async for raw in self._ws:
                data = json.loads(raw)
                req_id = data.get("req_id")
                msg_type = data.get("msg_type")

                # Resolve pending request futures FIRST
                if req_id is not None and req_id in self._pending:
                    future = self._pending.pop(req_id)
                    if not future.done():
                        future.set_result(data)

                # Handle subscription callbacks (for streamed messages without req_id)
                if msg_type and msg_type in self._subscriptions:
                    try:
                        callback = self._subscriptions[msg_type]
                        if asyncio.iscoroutinefunction(callback):
                            asyncio.create_task(callback(data))
                        else:
                            callback(data)
                    except Exception as e:
                        logger.error(f"Subscription callback error for {msg_type}: {e}")
                elif msg_type == "ohlc":
                    logger.warning(f"OHLC message received but no subscription handler registered!")
                elif msg_type and msg_type not in ("authorize", "balance", "ping", "candles", "buy", "proposal", "proposal_open_contract", "sell"):
                    logger.debug(f"Unhandled message type: {msg_type}")
        except websockets.ConnectionClosed as e:
            logger.warning(f"WebSocket connection closed: {e}")
            self._fail_pending(ConnectionError("WebSocket connection closed"))
        except Exception as e:
            logger.error(f"Listen error: {e}")
            self._fail_pending(e)
        finally:
            self._ws = None

    def _fail_pending(self, error: Exception):
        for req_id, future in list(self._pending.items()):
            if not future.done():
                future.set_exception(error)
            self._pending.pop(req_id, None)

    async def send(self, request: dict) -> dict:
        if not self.is_connected:
            raise RuntimeError("WebSocket not connected")

        self._req_id += 1
        req_id = self._req_id
        request["req_id"] = req_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        try:
            await self._ws.send(json.dumps(request))
            return await asyncio.wait_for(future, timeout=self._response_timeout)
        except Exception:
            self._pending.pop(req_id, None)
            if not future.done():
                future.cancel()
            raise

    def on_subscription(self, msg_type: str, callback: Callable):
        self._subscriptions[msg_type] = callback

    async def authorize(self) -> dict:
        """Authorize via legacy flow (not needed in PAT mode)."""
        if self._use_pat:
            # In PAT mode, authorization is handled by the OTP URL
            logger.info("PAT mode — skipping legacy authorize (already authenticated via OTP)")
            return {"authorize": {"loginid": self.account_id}}

        logger.info("Authorizing...")
        response = await self.send({"authorize": self.api_token})
        if "error" in response:
            logger.error(f"Authorization failed: {response['error']['message']}")
            raise RuntimeError(response["error"]["message"])
        auth = response.get("authorize", {})
        logger.info(f"Authorized: {auth.get('fullname', 'N/A')} | Balance: {auth.get('balance', 'N/A')} {auth.get('currency', 'N/A')}")
        return response

    async def get_balance(self) -> dict:
        response = await self.send({"balance": 1})
        if "error" in response:
            logger.error(f"Balance error: {response['error']['message']}")
            raise RuntimeError(response["error"]["message"])
        bal = response.get("balance", {})
        logger.info(f"Balance: {bal.get('balance', 'N/A')} {bal.get('currency', 'N/A')}")
        return response

    async def get_candles(self, symbol: str, granularity: int = 60, count: int = 250) -> list[dict]:
        """Fetch historical OHLC candles."""
        response = await self.send({
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "granularity": granularity,
            "style": "candles",
        })
        if "error" in response:
            logger.error(f"Candles error: {response['error']['message']}")
            raise RuntimeError(response["error"]["message"])
        candles = response.get("candles", [])
        logger.debug(f"Fetched {len(candles)} candles for {symbol}")
        return candles

    async def subscribe_candles(self, symbol: str, granularity: int, callback: Callable):
        """Subscribe to live OHLC candle stream."""
        self.on_subscription("ohlc", callback)
        request = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": 1,
            "end": "latest",
            "granularity": granularity,
            "style": "candles",
            "subscribe": 1,
        }
        response = await self.send(request)
        if "error" in response:
            logger.error(f"Candle subscription error: {response['error']['message']}")
            raise RuntimeError(response["error"]["message"])
        sub_id = response.get("subscription", {}).get("id")
        logger.info(f"Subscribed to {symbol} candles (granularity={granularity}s) [sub={sub_id}]")
        return response

    async def subscribe_ticks(self, symbol: str, callback: Callable):
        """Subscribe to real-time tick stream."""
        self.on_subscription("tick", callback)
        request = {"ticks": symbol, "subscribe": 1}
        response = await self.send(request)
        if "error" in response:
            logger.error(f"Tick subscription error: {response['error']['message']}")
            raise RuntimeError(response["error"]["message"])
        sub_id = response.get("subscription", {}).get("id")
        logger.info(f"Subscribed to {symbol} ticks [sub={sub_id}]")
        return response

    async def buy_contract(self, contract_type: str, symbol: str, amount: float, currency: str,
                           duration: int, duration_unit: str = "m",
                           basis: str = "stake") -> dict:
        """Buy a contract (CALL/PUT)."""
        request = {
            "buy": 1,
            "price": amount,
            "parameters": {
                "contract_type": contract_type,
                "symbol": symbol,
                "amount": amount,
                "basis": basis,
                "currency": currency,
                "duration": duration,
                "duration_unit": duration_unit,
            },
        }
        logger.info(f"Buying {contract_type} contract: {amount} {currency} | {duration}{duration_unit}")
        response = await self.send(request)
        if "error" in response:
            logger.error(f"Buy error: {response['error']['message']}")
            raise RuntimeError(response["error"]["message"])
        buy = response.get("buy", {})
        logger.info(f"Contract bought: id={buy.get('contract_id')} | buy_price={buy.get('buy_price')}")
        return response

    async def subscribe_profit_table(self, callback: Callable):
        """Subscribe to profit table updates."""
        self.on_subscription("profit_table", callback)
        response = await self.send({"profit_table": 1, "description": 1, "sort": "ASC", "subscribe": 1})
        return response

    async def subscribe_transaction(self, callback: Callable):
        """Subscribe to transaction updates."""
        self.on_subscription("transaction", callback)
        response = await self.send({"transaction": 1, "subscribe": 1})
        return response

    async def ping(self):
        return await self.send({"ping": 1})

    async def disconnect(self):
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        self._fail_pending(ConnectionError("WebSocket disconnected"))
        if self._ws:
            await self._ws.close()
            self._ws = None
            logger.info("WebSocket disconnected")

    @property
    def is_connected(self) -> bool:
        if self._ws is None:
            return False
        try:
            if hasattr(self._ws, 'closed'):
                return not self._ws.closed
            elif hasattr(self._ws, 'open'):
                return self._ws.open
            else:
                return hasattr(self._ws, 'send')
        except Exception:
            return False

    async def is_market_open(self, symbol: str) -> bool:
        """Check if the market is currently open for trading."""
        try:
            response = await self.send({
                "active_symbols": "brief",
            })
            if "error" in response:
                logger.error(f"Error checking market status: {response['error']['message']}")
                return False
            symbols = response.get("active_symbols", [])
            for sym in symbols:
                # New PAT API uses 'underlying_symbol' instead of 'symbol'
                sym_name = sym.get("underlying_symbol") or sym.get("symbol")
                if sym_name == symbol:
                    is_open = sym.get("is_trading_suspended", 0) == 0
                    exchange_is_open = sym.get("exchange_is_open", 0) == 1
                    market_open = is_open and exchange_is_open
                    logger.debug(f"Market {symbol} status: open={market_open}")
                    return market_open
            logger.warning(f"Symbol {symbol} not found in active symbols")
            return False
        except Exception as e:
            logger.error(f"Error checking market status: {e}")
            return False

    async def reconnect(self):
        """Reconnect WebSocket (re-obtains OTP if in PAT mode)."""
        logger.info("Reconnecting WebSocket...")
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

        self._fail_pending(ConnectionError("WebSocket reconnecting"))
        self._ws = None
        await self.connect()
        logger.info("WebSocket reconnected successfully")
