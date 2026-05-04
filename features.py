def normalize_rsi(value):
    return value / 100

def normalize_momentum(value, price):
    return abs(value) / price if price != 0 else 0

def normalize_volume(current_volume, avg_volume):
    return current_volume / avg_volume if avg_volume != 0 else 0

def normalize_volatility(current_volatility, avg_volatility):
    return current_volatility / avg_volatility if avg_volatility != 0 else 0