import subprocess
import sys
import time
import csv
import json
import os
from pathlib import Path
from math import isfinite
from imperator_monitor.run import finite_number, shared_text
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
from process_lock import exclusive_file_lock
from imperator_monitor.run import finite_number, shared_text
from uuid import uuid4
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

def paper_cycle_ready(project, run_id, started_at):
    try:
        with shared_text(project / "paper_status.json") as file:
            state = json.load(file)
    except (OSError, ValueError):
        return False

    if (
        not run_id
        or not isinstance(state, dict)
        or state.get("status") != "OK"
        or state.get("run_id") != run_id
    ):
        return False

    updated_at = finite_number(state.get("updated_at"), positive=True)
    return updated_at is not None and started_at <= updated_at <= time.time()
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
    paper_started_at = 0.0
    paper_run_id = None
    paper_ready = False
    paper_restart_at = None
    paper_restart_used = False

    def start_process(name):
        nonlocal paper_started_at, paper_run_id
        env = None

        if name == "paper":
            paper_started_at = time.time()
            paper_run_id = uuid4().hex
            env = os.environ.copy()
            env["IMPERATOR_PAPER_RUN_ID"] = paper_run_id

        return subprocess.Popen(
            [sys.executable, "-u", str(scripts[name])],
            cwd=str(project),
            env=env,
        )

    try:
        for name in scripts:
            process = start_process(name)
            processes[name] = process
            print(name, "started | PID:", process.pid, flush=True)


        while True:
            if not paper_restart_used and not paper_ready and "paper" not in stopped:
                paper_ready = paper_cycle_ready(project, paper_run_id, paper_started_at)
                if paper_ready:
                    print("paper ready | run_id:", paper_run_id, flush=True)
                    notify(
                        f"Paper first cycle completed at {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                        "Summary interval: 5 minutes"
                    )

            for name, process in processes.items():
                code = process.poll()
                if code is not None and name not in stopped:
                    stopped.add(name)
                    message = f"{name} STOPPED | exit code: {code}"
                    print(message, flush=True)
                    notify(message)

                    if (
                        name == "paper"
                        and code != 0
                        and paper_ready
                        and not paper_restart_used
                    ):
                        paper_restart_used = True
                        paper_restart_at = time.monotonic() + 5
                        print("paper restart scheduled | delay: 5s", flush=True)

            if paper_restart_at is not None and time.monotonic() >= paper_restart_at:
                paper_restart_at = None
                try:
                    processes["paper"] = start_process("paper")
                except OSError as error:
                    print("paper restart failed:", type(error).__name__, str(error), flush=True)
                else:
                    stopped.discard("paper")
                    print("paper restarted | PID:", processes["paper"].pid, flush=True)

            if len(stopped) == len(processes) and paper_restart_at is None:
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
    with exclusive_file_lock(Path(__file__).resolve(), "Bot supervisor"):
        main()