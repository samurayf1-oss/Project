import csv
from backtest import run_backtest

symbols = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
]

passed_symbols = []
failed_symbols = []

for symbol in symbols:
    print()
    print("=" * 50)
    print("SYMBOL:", symbol)
    print("=" * 50)

    try:
        result = run_backtest(symbol)

        if result["status"] == "PASSED":
            passed_symbols.append(result)
        else:
            failed_symbols.append(symbol)

    except Exception as error:
        print("ERROR:", symbol, error)
        failed_symbols.append(symbol)


print()
print("=" * 50)
print("FINAL SELECTION")
print("=" * 50)

print()
print("PASSED SYMBOLS:")

for result in passed_symbols:
    print(
        result["symbol"],
        "| confidence:",
        result["confidence"],
        "| trades:",
        result["trades"],
        "| total_return:",
        round(result["total_return"], 2),
        "%",
        "| drawdown:",
        round(result["max_drawdown"], 2),
        "%",
        "| profit_factor:",
        round(result["profit_factor"], 2)
    )

print()
print("FAILED SYMBOLS:")

for symbol in failed_symbols:
    print(symbol)

with open("selected_symbols.csv", "w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)

    writer.writerow([
        "symbol",
        "status",
        "confidence",
        "trades",
        "winrate",
        "total_return",
        "max_drawdown",
        "profit_factor"
    ])

    for result in passed_symbols:
        writer.writerow([
            result["symbol"],
        result["status"],
        result["confidence"],
        result["trades"],
        round(result["winrate"], 2),
        round(result["total_return"], 2),        
        round(result["max_drawdown"], 2),
        round(result["profit_factor"], 2)
        ])
    for symbol in failed_symbols:
        writer.writerow([
            symbol,
            "FAILED",
            ""
            "",
            "",
            "",
            "",
            ""
        ])
print()
print("Saved selection to selected_symbols.csv")