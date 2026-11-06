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
from time import sleep
from process_lock import exclusive_file_lock

SIGNALS_FILE = "live_signals.csv"

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

    for i in range(start_index, len(closes) - 5):
        price = closes[i]
        future_price = closes[i + 5]

        current_ema = ema_values[i - 13]
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


def get_live_signal(symbol, confidence, candles=None):
    if candles is None:
        candles = get_klines(
            symbol=symbol, interval="60", limit=1000, category="linear"
        )

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


def main(last_processed=None):
    selected_symbols = load_selected_symbols()
    signals = []
    processed = {}

    for item in selected_symbols:
        symbol = item["symbol"]
        confidence = item["confidence"]

        try:
            if not 0.5 <= confidence <= 1:
                raise ValueError("Confidence must be between 0.5 and 1")

            candles = get_klines(
                symbol=symbol, interval="60", limit=1000, category="linear"
            )
            candle_timestamp = int(candles[-1]["timestamp"])
            candle_key = (candle_timestamp, confidence)

            if (
                last_processed is not None
                and last_processed.get(symbol) == candle_key
            ):
                continue

            signal, signal_confidence, reason = get_live_signal(
                symbol, confidence, candles=candles
            )

            signal_time = datetime.fromtimestamp(
                (candle_timestamp + 3_600_000) / 1000
            )

            signals.append([
                signal_time.strftime("%Y-%m-%d %H:%M:%S"),
                symbol,
                signal,
                round(signal_confidence, 4),
                reason
            ])
            processed[symbol] = candle_key

        except Exception as error:
            print(
                symbol, "| ERROR |",
                type(error).__name__, str(error),
                flush=True
            )

    if not signals:
        return

    with open(SIGNALS_FILE, "a", newline="", encoding="utf-8") as file:
        csv.writer(file).writerows(signals)

    if last_processed is not None:
        last_processed.update(processed)

    print("\nLIVE SIGNALS")
    print("symbol | signal | confidence | reason")

    for _, symbol, signal, confidence, reason in signals:
        print(symbol, "|", signal, "|", confidence, "|", reason)

        print(f"Saved live signals to {SIGNALS_FILE}", flush=True)

if __name__ == "__main__":
    last_processed = {}

    try:
        with exclusive_file_lock(SIGNALS_FILE, "Signal writer"):
            while True:
                try:
                    main(last_processed)
                except Exception as error:
                    print(
                        "signal cycle failed:",
                        type(error).__name__,
                        str(error),
                        flush=True
                    )

                sleep(15)

    except KeyboardInterrupt:
        print("\nLive signals stopped.")