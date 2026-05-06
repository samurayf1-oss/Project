import csv
import os

TRADES_FILE = "paper_trades.csv"


def load_trades():
    if not os.path.exists(TRADES_FILE):
        print("paper_trades.csv not found")
        return []

    with open(TRADES_FILE, "r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        return list(reader)


def to_float(value):
    try:
        if value is None or value == "":
            return 0.0

        return float(value)
    except ValueError:
        return 0.0


def main():
    trades = load_trades()

    if not trades:
        print("No paper trades found.")
        return

    open_trades = [
        trade for trade in trades
        if trade["status"] == "OPEN"
    ]

    closed_trades = [
        trade for trade in trades
        if trade["status"] == "CLOSED"
    ]

    total_closed_pnl = sum(to_float(trade["pnl_usdt"]) for trade in closed_trades)
    total_open_pnl = sum(to_float(trade["pnl_usdt"]) for trade in open_trades)

    wins = [
        trade for trade in closed_trades
        if to_float(trade["pnl_usdt"]) > 0
    ]

    losses = [
        trade for trade in closed_trades
        if to_float(trade["pnl_usdt"]) <= 0
    ]

    winrate = len(wins) / len(closed_trades) * 100 if closed_trades else 0

    close_reasons = {}

    for trade in closed_trades:
        reason = trade.get("close_reason", "")

        if reason not in close_reasons:
            close_reasons[reason] = 0

        close_reasons[reason] += 1

    symbols = {}

    for trade in trades:
        symbol = trade["symbol"]

        if symbol not in symbols:
            symbols[symbol] = {
                "total": 0,
                "open": 0,
                "closed": 0,
                "pnl_usdt": 0.0
            }

        symbols[symbol]["total"] += 1

        if trade["status"] == "OPEN":
            symbols[symbol]["open"] += 1
        else:
            symbols[symbol]["closed"] += 1
            symbols[symbol]["pnl_usdt"] += to_float(trade["pnl_usdt"])

    print()
    print("PAPER REPORT")
    print("=" * 50)

    print("total trades:", len(trades))
    print("open trades:", len(open_trades))
    print("closed trades:", len(closed_trades))
    print("wins:", len(wins))
    print("losses:", len(losses))
    print("winrate:", round(winrate, 2), "%")
    print("closed pnl usdt:", round(total_closed_pnl, 4))
    print("open pnl usdt:", round(total_open_pnl, 4))

    print()
    print("CLOSE REASONS")
    print("=" * 50)

    if close_reasons:
        for reason, count in close_reasons.items():
            print(reason, ":", count)
    else:
        print("No closed trades yet.")

    print()
    print("SYMBOL STATS")
    print("=" * 50)

    for symbol, data in symbols.items():
        print(
            symbol,
            "| total:",
            data["total"],
            "| open:",
            data["open"],
            "| closed:",
            data["closed"],
            "| closed pnl usdt:",
            round(data["pnl_usdt"], 4)
        )


if __name__ == "__main__":
    main()