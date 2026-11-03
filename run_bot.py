import subprocess
import sys
import time
import csv
import os
from pathlib import Path
from math import isfinite
from imperator_monitor.run import shared_text
from paper_report import main as print_paper_report
from datetime import datetime
from telegram_notify import (
    send_message,
    can_send,
    diagnostics,
    format_trade_opened,
    format_trade_closed,
    format_cycle_summary,
    build_trade_summary,
)

TRADES_FILE = "paper_trades.csv"


def load_trades(allow_missing=False):
    try:
        with shared_text(TRADES_FILE) as file:
            reader = csv.DictReader(file)
            required = {
                "opened_at", "symbol", "side", "status", "pnl_usdt"
            }

            if not required.issubset(set(reader.fieldnames or [])):
                raise ValueError("Invalid paper_trades.csv header")

            trades = list(reader)

    except FileNotFoundError:
        if allow_missing:
            return []
        raise

    for trade in trades:
        if (
            None in trade
            or any(trade.get(key) is None for key in required)
            or not trade["opened_at"]
            or not trade["symbol"]
            or trade["side"] not in {"BUY", "SELL"}
            or trade["status"] not in {"OPEN", "CLOSED"}
        ):
            raise ValueError("Invalid paper_trades.csv row")

        if trade["status"] == "CLOSED":
            if not isfinite(float(trade["pnl_usdt"])):
                raise ValueError("Invalid closed PnL in paper_trades.csv")

    return trades


def trade_id(trade):
    return f"{trade.get('opened_at', '')}|{trade.get('symbol', '')}|{trade.get('side', '')}"


def detect_trade_events(before_trades, after_trades):
    before_by_id = {trade_id(trade): trade for trade in before_trades}
    after_by_id = {trade_id(trade): trade for trade in after_trades}

    opened_events = []
    closed_events = []

    for event_id, after_trade in after_by_id.items():
        if event_id not in before_by_id:
            if after_trade.get("status") == "OPEN":
                opened_events.append(after_trade)
            elif after_trade.get("status") == "CLOSED":
                closed_events.append(after_trade)
            continue

        if event_id in before_by_id:
            before_trade = before_by_id[event_id]
            if (
                before_trade.get("status") == "OPEN"
                and after_trade.get("status") == "CLOSED"
            ):
                closed_events.append(after_trade)

    return opened_events, closed_events

def notify(text):
    try:
        if can_send() and not send_message(text):
            print("Telegram send failed", flush=True)
    except Exception as error:
        print("Telegram error:", type(error).__name__, flush=True)


def main():
    project = Path(__file__).resolve().parent
    os.chdir(project)

    scripts = {
        "signals": project / "live.py",
        "paper": project / "paper_trading.py",
        "monitor": project / "imperator_monitor" / "run.py"
    }

    for path in scripts.values():
        if not path.is_file():
            raise SystemExit("Missing file: " + str(path))

    previous_trades = load_trades(allow_missing=True)
    processes = {}
    stopped = set()
    next_report = time.monotonic()

    try:
        for name, path in scripts.items():
            process = subprocess.Popen(
                [sys.executable, "-u", str(path)],
                cwd=str(project)
            )
            processes[name] = process
            print(name, "started | PID:", process.pid, flush=True)

        print(diagnostics())
        notify(
            f"Paper bot started at {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            "Summary interval: 5 minutes"
        )

        while True:
            for name, process in processes.items():
                code = process.poll()

                if code is not None and name not in stopped:
                    stopped.add(name)
                    message = f"{name} STOPPED | exit code: {code}"
                    print(message, flush=True)
                    notify(message)

            if len(stopped) == len(processes):
                break

            try:
                current_trades = load_trades()
            except (OSError, UnicodeError, csv.Error, ValueError) as error:
                print("Journal read failed:", str(error), flush=True)
                time.sleep(2)
                continue

            opened, closed = detect_trade_events(
                previous_trades, current_trades
            )
            previous_trades = current_trades

            for trade in opened:
                notify(format_trade_opened(trade))

            for trade in closed:
                notify(format_trade_closed(trade))

            if time.monotonic() >= next_report:
                next_report = time.monotonic() + 300

                try:
                    print_paper_report(current_trades)
                    summary = build_trade_summary(current_trades)
                    notify(format_cycle_summary(summary))
                except Exception as error:
                    print(
                        "Report failed:",
                        type(error).__name__,
                        flush=True
                    )

            time.sleep(2)

    except KeyboardInterrupt:
        print("\nStopping bot...", flush=True)

    finally:
        for process in processes.values():
            if process.poll() is None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.terminate()

        for process in processes.values():
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()