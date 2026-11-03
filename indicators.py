from math import isfinite
def ema(prices, period=14):
    if len(prices) < period:
        return []
    
    ema_values = []
    multiplier = 2 / (period + 1)

    sma = sum(prices[:period]) / period
    ema_values.append(sma)

    for price in prices[period:]:
        ema_value = (price - ema_values[-1]) * multiplier + ema_values[-1]
        ema_values.append(ema_value)

    return ema_values
    
def rsi(prices, period=14):
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise ValueError("RSI period must be a positive integer")

    prices = [float(value) for value in prices]
    if not all(isfinite(value) for value in prices):
        raise ValueError("RSI prices must be finite")

    if len(prices) <= period:
        return []

    changes = [
        prices[index] - prices[index - 1]
        for index in range(1, len(prices))
    ]
    if not all(isfinite(value) for value in changes):
        raise ValueError("RSI price changes must be finite")

    gains = [max(value, 0.0) for value in changes]
    losses = [max(-value, 0.0) for value in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def rsi_value(gain, loss):
        total = gain + loss
        if not isfinite(total):
            raise ValueError("RSI averages must be finite")
        return 50.0 if total == 0 else 100.0 * (gain / total)

    values = [rsi_value(avg_gain, avg_loss)]

    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        values.append(rsi_value(avg_gain, avg_loss))

    return values

def momentum(prices, period=5):
    values = []

    for i in range( period, len(prices)):
        values.append(prices[i] - prices[i - period])
    return values

def average_volume(candles, period=10):
    volumes = [candle["volume"] for candle in candles]
    avg_volumes = []

    for i in range(period, len(volumes)):
        avg = sum(volumes[i - period:i]) / period
        avg_volumes.append(avg)

    return avg_volumes

def volatility(candles, period=10):
    ranges = []
    volatilities = []

    for candle in candles:
        ranges.append(candle["high"] - candle["low"])

    for i in range(period, len(ranges)):
        avg_range = sum(ranges[i - period:i]) / period
        volatilities.append(avg_range)
    
    return volatilities

def market_regime(closes, period=20, threshold=0.015):
    regimes = []
    for i in  range(period, len(closes)):
        old_price = closes[i - period]
        current_price = closes[i]

        change = abs(current_price - old_price) / old_price
        if change > threshold:
            regimes.append("TREND")
        else:
            regimes.append("RANGE")
    return regimes