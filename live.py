import csv
from datetime import datetime
from data import get_klines
from indicators import ema, rsi, momentum, average_volume, volatility, market_regime
from features import (
    normalize_rsi,
    normalize_momentum,
    normalize_volume,
    normalize_volatility,
)
from model import train_model


def load_selected_symbols(filename="selected_symbols.csv"):
    symbols = []

    with open(filename, "r", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row in reader:
            if row["status"] == "PASSED":
                symbols.append({
                    "symbol": row["symbol"],
                    "confidence": float(row["confidence"])
                })

    return symbols


def build_dataset(candles):
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
            normalize_volatility(current_volatility, current_avg_volatility),
        ]

        target_return = (future_price - price) / price
        min_move = 0.002

        if target_return > min_move:
            label = 1
        elif target_return < -min_move:
            label = 0
        else:
            continue

        X.append(features)
        y.append(label)

    return X, y


def build_current_features(candles):
    closes = [candle["close"] for candle in candles]

    ema_values = ema(closes)
    rsi_values = rsi(closes)
    momentum_values = momentum(closes)
    avg_volumes = average_volume(candles)
    volatility_values = volatility(candles)
    regimes = market_regime(closes)

    i = len(candles) - 1

    price = closes[i]

    current_ema = ema_values[-1]
    current_rsi = rsi_values[-1]
    current_momentum = momentum_values[-1]
    current_volume = candles[i]["volume"]
    current_avg_volume = avg_volumes[-1]
    current_volatility = candles[i]["high"] - candles[i]["low"]
    current_avg_volatility = volatility_values[-1]
    current_regime = 1 if regimes[-1] == "TREND" else 0

    features = [
        current_regime,
        1 if price > current_ema else 0,
        normalize_rsi(current_rsi),
        normalize_momentum(current_momentum, price),
        normalize_volume(current_volume, current_avg_volume),
        normalize_volatility(current_volatility, current_avg_volatility),
    ]

    return features


def get_live_signal(symbol, confidence):
    candles = get_klines(symbol=symbol, limit=1000)

    X, y = build_dataset(candles)

    if len(X) == 0 or len(y) == 0:
        return "HOLD", 0, "empty dataset"

    if len(set(y)) < 2:
        return "HOLD", 0, "only one class in training data"

    model = train_model(X, y)

    current_features = build_current_features(candles)

    probability = model.predict_proba([current_features])[0]

    sell_confidence = probability[0]
    buy_confidence = probability[1]

    if buy_confidence >= confidence:
        return "BUY", buy_confidence, "passed confidence"

    if sell_confidence >= confidence:
        return "SELL", sell_confidence, "passed confidence"

    return "HOLD", max(buy_confidence, sell_confidence), "low confidence"


def main():
    selected_symbols = load_selected_symbols()

    print()
    print("LIVE SIGNALS")
    print("symbol | signal | confidence | reason")

    signals = []

    for item in selected_symbols:
        symbol = item["symbol"]
        confidence = item["confidence"]

        try:
            signal, signal_confidence, reason = get_live_signal(symbol, confidence)

            print(
                symbol,
                "|",
                signal,
                "|",
                round(signal_confidence, 4),
                "|",
                reason
            )

            signals.append([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                symbol,
                signal,
                round(signal_confidence, 4),
                reason
            ])

        except Exception as error:
            print(symbol, "| ERROR |", error)

    with open("live_signals.csv", "a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        for row in signals:
            writer.writerow(row)
    print()
    print("Saved live signals to live_signals.csv")

if __name__ == "__main__":
    main()