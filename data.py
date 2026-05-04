from pybit.unified_trading import HTTP
from config import API_KEY, API_SECRET

session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET
)
def get_price(symbol="BTCUSDT"):
    ticker = session.get_tickers(category="spot", symbol =symbol)
    return float(ticker["result"]["list"][0]["lastPrice"])

def get_klines(symbol="BTCUSDT", interval="60", limit=50):
    response = session.get_kline(
        category="spot",
        symbol=symbol,
        interval=interval,
        limit=limit
    )

    candles = response["result"]["list"]

    result = []
    for candle in candles:
        result.append({
            "time": candle[0],
            "open": float(candle[1]),
            "high": float(candle[2]),
            "low": float(candle[3]),
            "close": float(candle[4]),
            "volume": float(candle[5]),
        })
    return result