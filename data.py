from pybit.unified_trading import HTTP
from config import API_KEY, API_SECRET
from datetime import datetime, timezone
from math import isfinite
from time import time

session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET
)
market_session = HTTP(
    testnet=False,
    timeout=5,
    max_retries=2,
    retry_delay=1,
    force_retry=True
)
MAX_RESPONSE_AGE_SECONDS = 30
MAX_CLOCK_LEAD_SECONDS = 5
CANDLE_UPDATE_GRACE_MS = 2000


def _validate_market_response(response, category):
    if not isinstance(response, dict):
        raise RuntimeError("Invalid Bybit response type")

    if response.get("retCode") != 0:
        raise RuntimeError(
            f"Bybit error {response.get('retCode')}: {response.get('retMsg')}"
        )

    result = response.get("result")
    if not isinstance(result, dict) or result.get("category") != category:
        raise RuntimeError("Invalid Bybit result or category")

    if not isinstance(result.get("list"), list) or not result["list"]:
        raise RuntimeError("Bybit returned an empty or invalid data list")

    try:
        server_ms = int(response["time"])
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise RuntimeError("Invalid Bybit response timestamp") from error

    age_ms = int(time() * 1000) - server_ms
    if server_ms <= 0 or not (
        -MAX_CLOCK_LEAD_SECONDS * 1000
        <= age_ms
        <= MAX_RESPONSE_AGE_SECONDS * 1000
    ):
        raise RuntimeError("Bybit response is stale or local clock is incorrect")

    return result, server_ms


def get_price(symbol="BTCUSDT"):
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Symbol must be a non-empty string")

    symbol = symbol.strip().upper()

    response = market_session.get_tickers(category="linear", symbol=symbol)
    result, _ = _validate_market_response(response, "linear")
    items = result["list"]

    if len(items) != 1 or not isinstance(items[0], dict):
        raise RuntimeError(f"Unexpected ticker response for {symbol}")

    if items[0].get("symbol") != symbol:
        raise RuntimeError(f"Ticker symbol mismatch for {symbol}")

    try:
        price = float(items[0]["lastPrice"])
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid ticker price for {symbol}") from error

    if not isfinite(price) or price <= 0:
        raise ValueError(f"Invalid ticker price for {symbol}: {price!r}")

    return price
def _candle_end_ms(start_ms, interval):
    if interval == "M":
        start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        end = datetime(
            start.year + (start.month == 12),
            start.month % 12 + 1,
            1,
            tzinfo=timezone.utc
        )
        return int(end.timestamp() * 1000)

    if interval == "D":
        duration_ms = 24 * 60 * 60 * 1000
    elif interval == "W":
        duration_ms = 7 * 24 * 60 * 60 * 1000
    else:
        duration_ms = int(interval) * 60 * 1000

    return start_ms + duration_ms

def get_entry_price(symbol, *, max_spread_percent, min_turnover_24h_usdt):
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Symbol must be a non-empty string")

    symbol = symbol.strip().upper()
    if not symbol.endswith("USDT"):
        raise ValueError("Entry turnover check expects a USDT contract")

    max_spread_percent = float(max_spread_percent)
    min_turnover_24h_usdt = float(min_turnover_24h_usdt)

    if (
        not isfinite(max_spread_percent)
        or max_spread_percent <= 0
        or not isfinite(min_turnover_24h_usdt)
        or min_turnover_24h_usdt < 0
    ):
        raise ValueError("Invalid entry market limits")

    response = market_session.get_tickers(category="linear", symbol=symbol)
    result, _ = _validate_market_response(response, "linear")
    items = result["list"]

    if len(items) != 1 or not isinstance(items[0], dict):
        raise RuntimeError(f"Unexpected entry ticker response for {symbol}")

    ticker = items[0]
    if ticker.get("symbol") != symbol:
        raise RuntimeError(f"Entry ticker symbol mismatch for {symbol}")

    try:
        price = float(ticker["lastPrice"])
        bid = float(ticker["bid1Price"])
        ask = float(ticker["ask1Price"])
        turnover = float(ticker["turnover24h"])
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid entry ticker fields for {symbol}") from error

    if (
        not all(isfinite(value) for value in (price, bid, ask, turnover))
        or min(price, bid, ask) <= 0
        or ask <= bid
        or turnover < 0
    ):
        raise ValueError(f"Invalid entry market snapshot for {symbol}")

    midpoint = bid + (ask - bid) / 2
    spread_percent = (ask - bid) / midpoint * 100

    if spread_percent > max_spread_percent:
        raise ValueError(
            f"Spread {spread_percent:.4f}% exceeds {max_spread_percent:.4f}%"
        )

    if turnover < min_turnover_24h_usdt:
        raise ValueError(
            f"24h turnover {turnover:.2f} USDT "
            f"is below {min_turnover_24h_usdt:.2f} USDT"
        )

    return price

def get_klines(symbol="BTCUSDT", interval="60", limit=1000, category="linear", end_ms=None):
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Symbol must be a non-empty string")

    symbol = symbol.strip().upper()
    interval = str(interval).upper()

    if category not in {"linear", "inverse", "spot"}:
        raise ValueError(f"Unsupported category: {category}")

    if interval not in {
        "1", "3", "5", "15", "30", "60", "120",
        "240", "360", "720", "D", "W", "M"
    }:
        raise ValueError(f"Unsupported interval: {interval}")

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("Limit must be an integer from 1 to 1000")

    if end_ms is not None and (
        isinstance(end_ms, bool)
        or not isinstance(end_ms, int)
        or end_ms <= 0
    ):
        raise ValueError("end_ms must be a positive integer in milliseconds")

    params = {
        "category": category,
        "symbol": symbol,
        "interval": interval,
        "limit": min(limit + 1, 1000)
    }
    if end_ms is not None:
        params["end"] = end_ms - 1

    response = market_session.get_kline(**params)
    result, server_ms = _validate_market_response(response, category)

    cutoff_ms = server_ms if end_ms is None else end_ms
    if cutoff_ms > server_ms:
        raise ValueError("end_ms cannot be later than server time")

    if result.get("symbol") != symbol:
        raise RuntimeError(f"Candle symbol mismatch for {symbol}")

    candles = []
    seen = set()

    for item in result["list"]:
        if not isinstance(item, (list, tuple)) or len(item) < 7:
            raise ValueError(f"Invalid candle row for {symbol}")

        try:
            timestamp = int(item[0])
            values = [float(value) for value in item[1:7]]
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"Invalid candle values for {symbol}") from error

        open_price, high, low, close, volume, turnover = values

        if not all(isfinite(value) for value in values):
            raise ValueError(f"Non-finite candle values for {symbol}")

        if (
            timestamp <= 0
            or timestamp > server_ms
            or timestamp in seen
            or min(open_price, high, low, close) <= 0
            or volume < 0
            or turnover < 0
            or not low <= open_price <= high
            or not low <= close <= high
        ):
            raise ValueError(f"Invalid candle data for {symbol}: {timestamp}")

        seen.add(timestamp)

        if _candle_end_ms(timestamp, interval) > cutoff_ms:
            continue

        candles.append({
            "timestamp": timestamp,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "turnover": turnover
        })

    if not candles:
        raise RuntimeError(f"No closed candles for {symbol}")

    candles.sort(key=lambda candle: candle["timestamp"])
    candles = candles[-limit:]

    for previous, current in zip(candles, candles[1:]):
        if _candle_end_ms(previous["timestamp"], interval) != current["timestamp"]:
            raise RuntimeError(f"Candle gap detected for {symbol}")

    latest_end = _candle_end_ms(candles[-1]["timestamp"], interval)
    if cutoff_ms >= _candle_end_ms(latest_end, interval) + CANDLE_UPDATE_GRACE_MS:
        raise RuntimeError(f"Missing latest closed candle at requested time for {symbol}")

    return candles

def get_historical_klines(
    symbol="BTCUSDT", interval="60", total=4320,
    category="linear", end_ms=None
):
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        raise ValueError("total must be a positive integer")

    interval = str(interval).upper()
    candles = []
    cursor = end_ms

    while len(candles) < total:
        page = get_klines(
            symbol=symbol,
            interval=interval,
            limit=min(1000, total - len(candles)),
            category=category,
            end_ms=cursor
        )

        if cursor is not None and page[-1]["timestamp"] >= cursor:
            raise RuntimeError("Historical pagination did not move backwards")

        if candles and (
            _candle_end_ms(page[-1]["timestamp"], interval)
            != candles[0]["timestamp"]
        ):
            raise RuntimeError("Candle gap detected between historical pages")

        candles = page + candles
        cursor = page[0]["timestamp"]

    return candles