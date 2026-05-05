import csv
import os
from datetime import datetime

from data import get_price


SIGNALS_FILE = "live_signals.csv"
TRADES_FILE = "paper_trades.csv"

TAKE_PROFIT_PERCENT = 1.5
STOP_LOSS_PERCENT = 1.0


def load_latest_signals():
    if not os.path.exists(SIGNALS_FILE):
        print("ERROR: live_signals.csv not found")
        return []

    signals = {}

    with open(SIGNALS_FILE, "r", encoding="utf-8") as file:
        reader = csv.reader(file)

        for row in reader:
            if len(row) < 5:
                continue

            timestamp = row[0]
            symbol = row[1]
            signal = row[2]
            confidence = row[3]
            reason = row[4]

            signals[symbol] = {
                "timestamp": timestamp,
                "symbol": symbol,
                "signal": signal,
                "confidence": confidence,
                "reason": reason
            }

    return list(signals.values())


def load_trades():
    if not os.path.exists(TRADES_FILE):
        return []

    trades = []

    with open(TRADES_FILE, "r", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row in reader:
            trades.append(row)

    return trades

def has_open_trade(symbol, open_trades):
    for trade in open_trades:
        if trade["symbol"] == symbol:
            return True

    return False

def calculate_pnl_percent(side, entry_price, exit_price):
    entry_price = float(entry_price)
    exit_price = float(exit_price)

    if side == "BUY":
        return (exit_price - entry_price) / entry_price * 100

    if side == "SELL":
        return (entry_price - exit_price) / entry_price * 100

    return 0

def close_trade(trade, exit_price, reason):
    pnl_percent = calculate_pnl_percent(
        trade["side"],
        trade["entry_price"],
        exit_price
    )

    trade["status"] = "CLOSED"
    trade["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    trade["exit_price"] = exit_price
    trade["pnl_percent"] = round(pnl_percent, 4)
    trade["close_reason"] = reason

    return pnl_percent

def save_all_trades(trades):
    fieldnames = [
        "opened_at",
        "symbol",
        "side",
        "entry_price",
        "confidence",
        "status",
        "closed_at",
        "exit_price",
        "pnl_percent",
        "close_reason"
    ]

    with open(TRADES_FILE, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for trade in trades:
            writer.writerow(trade)

def append_trade(trade):
    file_exists = os.path.exists(TRADES_FILE)

    with open(TRADES_FILE, "a", newline="", encoding="utf-8") as file:
        fieldnames = [
            "opened_at",
            "symbol",
            "side",
            "entry_price",
            "confidence",
            "status",
            "closed_at",
            "exit_price",
            "pnl_percent",
            "close_reason"
        ]

        writer = csv.DictWriter(file, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        writer.writerow(trade)


def main():
    latest_signals = load_latest_signals()
    trades = load_trades()
    open_trades = [trade for trade in trades if trade["status"] == "OPEN"]

    print()
    print("PAPER TRADING")
    print("symbol | signal | action | price")

    for item in latest_signals:
        symbol = item["symbol"]
        signal = item["signal"]
        confidence = item["confidence"]

        if signal == "HOLD":
            print(symbol, "| HOLD | no trade | -")
            continue

        for trade in open_trades:
            if trade["symbol"] != symbol:
                continue

            current_price = get_price(symbol)

            current_pnl = calculate_pnl_percent(
                trade["side"],
                trade["entry_price"],
                current_price
            )

            if current_pnl >= TAKE_PROFIT_PERCENT:
                close_trade(trade, current_price, "TAKE_PROFIT")
                print(symbol, "|", signal, "| closed by take profit |", current_price)
                save_all_trades(trades)
                continue

            if current_pnl <= -STOP_LOSS_PERCENT:
                close_trade(trade, current_price, "STOP_LOSS")
                print(symbol, "|", signal, "| closed by stop loss |", current_price)
                save_all_trades(trades)
                continue

            if trade["side"] != signal:
                close_trade(trade, current_price, "OPPOSITE_SIGNAL")
                print(symbol, "|", signal, "| closed by opposite signal |", current_price)
                save_all_trades(trades)
                continue

        open_trades = [trade for trade in load_trades() if trade["status"] == "OPEN"]

        if has_open_trade(symbol, open_trades):
            for trade in open_trades:
                if trade["symbol"] == symbol:
                    current_price = get_price(symbol)

                    current_pnl = calculate_pnl_percent(
                        trade["side"],
                        trade["entry_price"],
                        current_price
                    )

                    print(
                        symbol,
                        "|",
                        signal,
                        "| already open |",
                        current_price,
                        "| pnl:",
                        round(current_pnl, 4),
                        "%"
                    )

                    break

            continue

        price = get_price(symbol)

        trade = {
            "opened_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "side": signal,
            "entry_price": price,
            "confidence": confidence,
            "status": "OPEN",
            "closed_at": "",
            "exit_price": "",
            "pnl_percent": "",
            "close_reason": ""
        }

        append_trade(trade)

        print(symbol, "|", signal, "| opened paper trade |", price)

    print()
    print("OPEN TRADES SUMMARY")
    print("symbol | side | entry | current | pnl")

    updated_trades = load_trades()
    updated_open_trades = [trade for trade in updated_trades if trade["status"] == "OPEN"]

    for trade in updated_open_trades:
        symbol = trade["symbol"]
        current_price = get_price(symbol)

        current_pnl = calculate_pnl_percent(
            trade["side"],
            trade["entry_price"],
            current_price
        )

        print(
            symbol,
            "|",
            trade["side"],
            "|",
            trade["entry_price"],
            "|",
            current_price,
            "|",
            round(current_pnl, 4),
            "%"
        )
        
if __name__ == "__main__":
    main()