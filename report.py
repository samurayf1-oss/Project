import csv
import os


TRADES_FILE = "paper_trades.csv"


def load_trades():
    if not os.path.exists(TRADES_FILE):
        print("ERROR: paper_trades.csv not found")
        return []

    trades = []

    with open(TRADES_FILE, "r", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row in reader:
            trades.append(row)

    return trades


def main():
    trades = load_trades()

    closed_trades = [
        trade for trade in trades
        if trade["status"] == "CLOSED" and trade["pnl_percent"] != ""
    ]

    open_trades = [
        trade for trade in trades
        if trade["status"] == "OPEN"
    ]

    print()
    print("PAPER TRADING REPORT")
    print("=" * 50)

    print("total trades:", len(trades))
    print("open trades:", len(open_trades))
    print("closed trades:", len(closed_trades))

    if len(closed_trades) == 0:
        print()
        print("No closed trades yet.")
        return

    pnl_values = [float(trade["pnl_percent"]) for trade in closed_trades]

    pnl_usdt_values = []

    for trade in closed_trades:
        if "pnl_usdt" in trade and trade["pnl_usdt"] != "":
            pnl_usdt_values.append(float(trade["pnl_usdt"]))

    wins = [pnl for pnl in pnl_values if pnl > 0]
    losses = [pnl for pnl in pnl_values if pnl <= 0]

    winrate = len(wins) / len(closed_trades) * 100

    total_pnl = sum(pnl_values)
    average_pnl = total_pnl / len(closed_trades)

    total_pnl_usdt = sum(pnl_usdt_values) if pnl_usdt_values else 0

    best_trade = max(pnl_values)
    worst_trade = min(pnl_values)

    print()
    print("CLOSED TRADES STATS")
    print("wins:", len(wins))
    print("losses:", len(losses))
    print("winrate:", round(winrate, 2), "%")
    print("total pnl:", round(total_pnl, 4), "%")
    print("total pnl usdt:", round(total_pnl_usdt, 4), "USDT")
    print("average pnl:", round(average_pnl, 4), "%")
    print("best trade:", round(best_trade, 4), "%")
    print("worst trade:", round(worst_trade, 4), "%")

    print()
    print("CLOSED TRADES")
    print("symbol | side | entry | exit | pnl % | pnl usdt | reason")

    for trade in closed_trades:
        print(
            trade["symbol"],
            "|",
            trade["side"],
            "|",
            trade["entry_price"],
            "|",
            trade["exit_price"],
            "|",
            trade["pnl_percent"],
            "%",
            "|",
            trade.get("pnl_usdt", ""),
            "USDT",
            "|",
            trade.get("close_reason", "")
        )


if __name__ == "__main__":
    main()