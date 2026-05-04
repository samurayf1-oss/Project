from data import get_klines
from indicators import ema, rsi, momentum, average_volume, volatility, market_regime
from features import (
    normalize_rsi,
    normalize_momentum,
    normalize_volume,
    normalize_volatility
)
from model import train_model, predict_signal, show_feature_importance

def run_backtest(symbol="BTCUSDT"):
    candles = get_klines(symbol, limit=200)
    closes = [candle["close"] for candle in candles]

    ema_values = ema(closes)
    rsi_values = rsi(closes)
    momentum_values = momentum(closes)
    avg_volumes = average_volume(candles)
    volatility_values = volatility(candles)
    regimes = market_regime(closes)

    X = []
    y = []

    start_index = 20

    for i in range(start_index, len(ema_values) - 5):
        price = closes[i]
        future_price = closes[i + 5]

        current_ema = ema_values[i - 14]
        current_rsi = rsi_values[i - 14]
        current_momentum = momentum_values[i - 5]
        current_volume = candles[i]["volume"]
        current_avg_volume = avg_volumes[i - 10]
        current_volatility = candles[i]["high"] - candles[i]["low"]
        current_avg_volatility = volatility_values[i - 10]
        current_regime = 1 if regimes[i - 20] == "TREND" else 0


        features = [ 
            current_regime,
            1 if price > current_ema else 0, 
            normalize_rsi(current_rsi),  
            normalize_momentum(current_momentum, price),
            normalize_volume(current_volume, current_avg_volume),
            normalize_volatility(current_volatility, current_avg_volatility)
        ]

        label = 1 if future_price > price else 0

        X.append(features)
        y.append(label)
    
    split = int(len(X) * 0.8)

    X_train = X[:split]
    y_train = y[:split]
    X_test = X[split:]
    y_test = y[split:]
    
    print("DATASET SIZE:",len(X))
    print("TRAIN SIZE:", len(X_train))
    print("TEST SIZE:", len(X_test))
    
    model = train_model(X_train, y_train)
    show_feature_importance(model)
    wins = 0
    losses = 0

    for features, real in zip(X_test, y_test):
        signal, confidence = predict_signal(model, features)

        pred = 1 if signal == "BUY" else 0

        if pred == real:
            wins += 1
        else:
            losses += 1

    total = wins + losses
    winrate = (wins / total * 100) if total > 0 else 0

    print("BACKTEST RESULT")
    print("Wins:", wins)
    print("Losses:", losses)
    print("Winrate:", round(winrate, 2), "%")