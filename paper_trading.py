import csv
import os
from datetime import datetime

from data import get_price

SIGNALS_FILE = "live_signals.csv"
TRADES_FILE = "paper_trades.csv"

TRADING_MODE = "futures"    # "spot" или "futures"
TAKE_PROFIT_PERCENT = 0.7
STOP_LOSS_PERCENT = 0.4

POZITION_SIZE_USDT = 100
LEVERAGE = 3

MAX_OPEN_TRADES = 3
MAXDAILY_LOSS_USDT = -3
MAX_HOLD_MINUTES = 60

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

def calculate_pnl_usdt(pnl_percent):
    if TRADING_MODE == "futures":
        return POZITION_SIZE_USDT * (pnl_percent * LEVERAGE) / 100
    return POZITION_SIZE_USDT * pnl_percent / 100

def calculate_trade_age(opened_at):
    opened_time = datetime.strptime(opened_at, "%Y-%m-%d %H:%M:%S")
    current_time = datetime.now()

    delta = current_time - opened_time

    total_minutes = int(delta.total_seconds() // 60)

    hours = total_minutes // 60
    minutes = total_minutes % 60

    return f"{hours}h {minutes}m"

def calculate_trade_age_minutes(opened_at):
    opened_time = datetime.strptime(opened_at, "%Y-%m-%d %H:%M:%S")
    current_time = datetime.now()

    delta = current_time - opened_time

    return int(delta.total_seconds() // 60)

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
    trade["pnl_usdt"] = round(calculate_pnl_usdt(pnl_percent), 4)
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
        "pnl_usdt",
        "close_reason"
    ]

    with open(TRADES_FILE, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for trade in trades:
            writer.writerow(trade)

def get_today_closed_pnl_usdt():
    trades = load_trades()

    today = datetime.now().strftime("%Y-%m-%d")
    daily_pnl = 0

    for trade in trades:
        if trade["status"] != "CLOSED":
            continue
        
        if trade["closed_at"] == "":
            continue

        if not trade["closed_at"].startswith(today):
            continue

        if "pnl_usdt" not in trade or trade["pnl_usdt"] == "":
            continue

        daily_pnl += float(trade["pnl_usdt"])
    return daily_pnl

def print_portfolio_summary():
    trades = load_trades()

    open_trades = [trade for trade in trades if trade["status"] == "OPEN"]
    closed_trades = [trade for trade in trades if trade["status"] == "CLOSED"]

    open_pnl = 0
    open_pnl_usdt = 0

    for trade in open_trades:
        symbol = trade["symbol"]
        current_price = get_price(symbol)

        current_pnl = calculate_pnl_percent(
            trade["side"],
            trade["entry_price"],
            current_price
        )

        open_pnl += current_pnl
        open_pnl_usdt += calculate_pnl_usdt(current_pnl)


    closed_pnl = 0
    closed_pnl_usdt = 0

    for trade in closed_trades:
        if trade["pnl_percent"] == "":
            continue

        closed_pnl += float(trade["pnl_percent"])
        
        if "pnl_usdt" in trade and trade["pnl_usdt"] != "":
            closed_pnl_usdt += float(trade["pnl_usdt"])

    total_pnl = open_pnl + closed_pnl
    total_pnl_usdt = open_pnl_usdt + closed_pnl_usdt
    
    today_closed_pnl_usdt = get_today_closed_pnl_usdt()
    
    if today_closed_pnl_usdt <= MAXDAILY_LOSS_USDT:
        daily_risk_status = "BLOCKED"
    else:
        daily_risk_status = "ACTIV"


    print()
    print("PORTFOLIO SUMMARY")
    print("open trades:", len(open_trades))
    print("closed trades:", len(closed_trades))
    print("open pnl:", round(open_pnl, 4), "%")
    print("open pnl_usdt:", round(open_pnl_usdt, 4), "USDT")
    print("closed pnl:", round(closed_pnl, 4), "%")
    print("closed pnl_usdt:", round(closed_pnl_usdt, 4), "USDT")
    print("total pnl:", round(total_pnl, 4), "%")
    print("total pnl_usdt:", round(total_pnl_usdt, 4), "USDT")
    print("daily closed pnl_usdt:", round(today_closed_pnl_usdt, 4), "USDT")
    print("daily loss limit:", MAXDAILY_LOSS_USDT, "USDT")
    print("daily risk status:", daily_risk_status)

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
            "pnl_usdt",
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
        
        trade_was_closed = False

        for trade in open_trades:
            if trade["symbol"] != symbol:
                continue

            current_price = get_price(symbol)

            current_pnl = calculate_pnl_percent(
                trade["side"],
                trade["entry_price"],
                current_price
            )

            trade_age_minutes = calculate_trade_age_minutes(trade["opened_at"])

            if trade_age_minutes >= MAX_HOLD_MINUTES:
                close_trade(trade, current_price, "TIME_EXIT")
                print(symbol, "|", signal, "| closed by time exit |", current_price)
                save_all_trades(trades)
                trade_was_closed = True
                break

            if current_pnl >= TAKE_PROFIT_PERCENT:
                close_trade(trade, current_price, "TAKE_PROFIT")
                print(symbol, "|", signal, "| closed by take profit |", current_price)
                save_all_trades(trades)
                trade_was_closed = True
                break

            if current_pnl <= -STOP_LOSS_PERCENT:
                close_trade(trade, current_price, "STOP_LOSS")
                print(symbol, "|", signal, "| closed by stop loss |", current_price)
                save_all_trades(trades)
                trade_was_closed = True
                break

            if trade["side"] != signal:
                close_trade(trade, current_price, "OPPOSITE_SIGNAL")
                print(symbol, "|", signal, "| closed by opposite signal |", current_price)
                save_all_trades(trades)
                trade_was_closed = True
                break

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
        
        if TRADING_MODE == "spot" and signal == "SELL":
            print(symbol , "| SELL | ignored on spot | -")
            continue
        
        open_trades = [trade for trade in load_trades() if trade["status"] == "OPEN"]
        
        if len(open_trades) >= MAX_OPEN_TRADES:
            print(symbol, "|", signal, "| skipped max open trades | -")
            continue
        
        today_pnl_usdt = get_today_closed_pnl_usdt()
        
        if today_pnl_usdt <= MAXDAILY_LOSS_USDT:
            print(symbol, "|", signal, "| skipped daily loss limit |", round(today_pnl_usdt, 4), "USDT")

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
            "pnl_usdt": "",
            "close_reason": ""
        }

        append_trade(trade)

        print(symbol, "|", signal, "| opened paper trade |", price)

    print()
    print("OPEN TRADES SUMMARY")
    print("symbol | side | entry | current | pnl | pnl USDT | age | status")

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
        
        trade_age = calculate_trade_age(trade["opened_at"])
        current_pnl_usdt = calculate_pnl_usdt(current_pnl)

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
            "%",
            "|",
            round(current_pnl_usdt, 4),
            "USDT",
            "|",
            trade_age,
            "|",
            "WAITING"
        )
        
    print_portfolio_summary()
    

if __name__ == "__main__":
    main()