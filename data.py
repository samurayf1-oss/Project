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

def get_klines(symbol="BTCUSDT", interval="60", limit=1000):
    response = session.get_kline(
        category="linear",
        symbol=symbol,
        interval=interval,
        limit=limit
    )

    raw_candles = response["result"]["list"]
    
    candles = []

    for item in raw_candles:
         candles.append({
              "timestamp": int(item[0]),
              "open": float(item[1]),
              "high": float(item[2]),
              "low": float(item[3]),
              "close": float(item[4]),
              "volume": float(item[5]),
              "turnover": float(item[6])
         })
    
    candles.reverse()

    return candles