import subprocess
import sys
import time
import csv
import os
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


def load_trades():
    if not os.path.exists(TRADES_FILE):
        return []

    with open(TRADES_FILE, "r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        return list(reader)


def trade_id(trade):
    return f"{trade.get('opened_at', '')}|{trade.get('symbol', '')}|{trade.get('side', '')}"


def detect_trade_events(before_trades, after_trades):
    before_by_id = {trade_id(trade): trade for trade in before_trades}
    after_by_id = {trade_id(trade): trade for trade in after_trades}

    opened_events = []
    closed_events = []

    for event_id, after_trade in after_by_id.items():
        if event_id not in before_by_id and after_trade.get("status") == "OPEN":
            opened_events.append(after_trade)
            continue

        if event_id in before_by_id:
            before_trade = before_by_id[event_id]
            if (
                before_trade.get("status") == "OPEN"
                and after_trade.get("status") == "CLOSED"
            ):
                closed_events.append(after_trade)

    return opened_events, closed_events

def run_script(script_name):
    print()
    print("=" * 50)
    print("RUNNING:", script_name)
    print("=" * 50)

    result = subprocess.run(
        [sys.executable, script_name],
        text=True
    )

    if result.returncode != 0:
        print("ERROR:", script_name, "failed")
        return False

    return True


def main():
    interval_minutes = 5
    interval_seconds = interval_minutes * 60

    print(diagnostics())

    if can_send():
        started_sent = send_message(
            f"Bot started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Cycle interval: {interval_minutes} minutes"
        )
        if not started_sent:
            print("Telegram send failed: check token/chat_id and bot access to chat")

    while True:
        print()
        print("=" * 50)
        print("BOT CYCLE START")
        print("=" * 50)
        cycle_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if can_send():
            cycle_started_sent = send_message(f"Cycle started: {cycle_started_at}")
            if not cycle_started_sent:
                print("Telegram send failed at cycle start")

        live_ok = run_script("live.py")

        if not live_ok:
            print("Bot cycle stopped because live.py failed")
            if can_send():
                    send_message("ERROR: live.py failed")
        else:
            trades_before = load_trades()
            paper_ok = run_script("paper_trading.py")

            if not paper_ok:
                print("Bot cycle stopped because paper_trading.py failed")
                if can_send():
                    send_message("ERROR: paper_trading.py failed")
            else:
                trades_after = load_trades()
                opened_events, closed_events = detect_trade_events(trades_before, trades_after)

                if can_send():
                    for trade in opened_events:
                        send_message(format_trade_opened(trade))
                    for trade in closed_events:
                        send_message(format_trade_closed(trade))

                report_ok = run_script("paper_report.py")
                if not report_ok:
                    print("Paper report failed")
                    if can_send():
                        send_message("ERROR: paper_report.py failed")
                elif can_send():
                    summary = build_trade_summary(trades_after)
                    send_message(format_cycle_summary(summary))

        print()
        print("=" * 50)
        print("BOT CYCLE COMPLETE")
        print("Next run in", interval_minutes, "minutes")
        print("=" * 50)

        if can_send():
            cycle_complete_sent = send_message(f"Cycle complete. Next run in {interval_minutes} minutes.")
            if not cycle_complete_sent:
                print("Telegram send failed at cycle complete")

        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()