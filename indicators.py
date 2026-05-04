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
    gains = []
    losses = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        if change >0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rs = avg_gain / avg_loss if avg_loss != 0 else 0
    rsi_values = [100 - (100 / (1 + rs))]

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

        rs = avg_gain / avg_loss if avg_loss !=0 else 0
        rsi_values.append(100 - (100 / (1 + rs)))

    return rsi_values

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

def market_regime(prices, period=20, threshold=0.015):
    regimes = []
    for i in  range(period, len(prices)):
        start_price = prices[i - period]
        end_price = prices[i]

        change = abs(end_price - start_price) / start_price
        if change > threshold:
            regimes.append("TREND")
        else:
            regimes.append("RANGE")
    return regimes