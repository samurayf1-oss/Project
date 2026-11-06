import csv
import os
from tempfile import NamedTemporaryFile

from process_lock import exclusive_file_lock

SELECTION_FILE = "selected_symbols.csv"


def select_symbols():
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

            if (
                result.get("status") == "PASSED"
                and result.get("walk_status") == "PASSED"
                and result.get("final_status") == "PASSED"
            ):
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

    symbol_selection_status = "PASSED" if len(passed_symbols) > 0 else "FAILED"
    final_bot_status = symbol_selection_status

    print("SYMBOL_SELECTION_STATUS:", symbol_selection_status)
    print("FINAL_BOT_STATUS:", final_bot_status)
    if final_bot_status == "FAILED":
        print("WARNING: No fully validated symbols. Use paper trading / observation only.")
        print()

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

    return passed_symbols, failed_symbols


def save_selection(passed_symbols, failed_symbols):
    selection_path = os.path.abspath(SELECTION_FILE)
    temp_path = None

    try:
        with NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8",
            dir=os.path.dirname(selection_path),
            prefix="selected_symbols_",
            suffix=".tmp",
            delete=False,
        ) as file:
            temp_path = file.name
            writer = csv.writer(file)

            writer.writerow([
                "symbol",
                "status",
                "confidence",
                "trades",
                "winrate",
                "total_return",
                "max_drawdown",
                "profit_factor",
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
                    round(result["profit_factor"], 2),
                ])

            for symbol in failed_symbols:
                writer.writerow([symbol, "FAILED", "", "", "", "", "", ""])

            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_path, selection_path)
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.remove(temp_path)


if __name__ == "__main__":
    with exclusive_file_lock(SELECTION_FILE, "Selection writer"):
        save_selection([], [])
        print("Selection started | new entries paused", flush=True)
        passed_symbols, failed_symbols = select_symbols()
        save_selection(passed_symbols, failed_symbols)

        print()
        print(f"Saved selection to {SELECTION_FILE}")
