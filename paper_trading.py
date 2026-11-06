from contextlib import contextmanager
import csv
import json
import os
from datetime import datetime, timezone
from settings import (
    STOP_LOSS,
    TAKE_PROFIT,
    LEVERAGE,
    COMMISSION,
    MAX_HOLD_MINUTES,
    HOLD_EXTENSION_MINUTES,
)
from data import get_price, get_entry_price
from math import floor, isfinite
from tempfile import NamedTemporaryFile
from time import sleep, monotonic
from process_lock import exclusive_file_lock
from argparse import ArgumentParser
from time import sleep, monotonic, time

SELECTED_SYMBOLS_FILE = "selected_symbols.csv"
SIGNALS_FILE = "live_signals.csv"
TRADES_FILE = "paper_trades.csv"

TRADING_MODE = "futures"    # "spot" или "futures"
# TAKE_PROFIT_PERCENT = 0.7
# STOP_LOSS_PERCENT = 0.4
POZITION_SIZE_USDT = 100
PAPER_INITIAL_BALANCE_USDT = 100.0
PAPER_MAX_DRAWDOWN_PERCENT = 15.0
PAPER_ACCOUNT_STATE_FILE = "paper_account_state.json"
LEGACY_POSITION_SIZE_USDT = 100
LEGACY_LEVERAGE = 3
LEGACY_FEE_RATE = 0.0  # Прежняя бумажная модель не учитывала комиссию.
# LEVERAGE = 3
MAX_RISK_PER_TRADE_USDT = 1.0
MAX_TOTAL_OPEN_RISK_USDT = 3.0
MAX_TOTAL_ENTRY_NOTIONAL_USDT = 900.0
MAX_ALLOWED_LEVERAGE = 3.0
PAPER_CHECK_INTERVAL_SECONDS = 15
MAX_ENTRY_SPREAD_PERCENT = 0.10
MIN_ENTRY_TURNOVER_24H_USDT = 10_000_000.0

MAX_OPEN_TRADES = 3
MAXDAILY_LOSS_USDT = -3
MAX_SIGNAL_AGE_MINUTES = 10
REENTRY_COOLDOWN_MINUTES = 15

def load_latest_signals():
    if not os.path.exists(SIGNALS_FILE):
        print("ERROR: live_signals.csv not found")
        return []

    signals = {}

    with open(SIGNALS_FILE, "r", newline="", encoding="utf-8") as file:
        for row in csv.reader(file):
            if len(row) < 5:
                if len(row) >= 2:
                    signals.pop(row[1], None)
                continue

            timestamp, symbol, signal, confidence, reason = row[:5]
            signals[symbol] = {
                "timestamp": timestamp,
                "symbol": symbol,
                "signal": signal,
                "confidence": confidence,
                "reason": reason
            }
    now = datetime.now()
    fresh_signals = []
    for item in signals.values():
        try:
            signal_time = datetime.strptime(
                item["timestamp"], "%Y-%m-%d %H:%M:%S"
            )
            confidence = float(item["confidence"])
        except (TypeError, ValueError):
            print(item["symbol"], "| skipped invalid signal data")
            continue

        age_seconds = (now - signal_time).total_seconds()

        if(
            not item["symbol"]
            or item["signal"] not in {"BUY", "SELL", "HOLD"}
            or not isfinite(confidence)
            or not 0 <= confidence <= 1
            or not 0 <= age_seconds <= MAX_SIGNAL_AGE_MINUTES * 60
        ):
            print(item["symbol"], "| skipped invalid or stale signal")
            continue
        fresh_signals.append(item)
    return fresh_signals

def load_selected_symbols():
    selected_symbols = []

    if not os.path.exists(SELECTED_SYMBOLS_FILE):
        print("ERROR: selected_symbols.csv not found")
        return selected_symbols

    with open(SELECTED_SYMBOLS_FILE, "r", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row in reader:
            if row["status"] != "PASSED":
                continue

            selected_symbols.append(row["symbol"])

    return selected_symbols

def load_trades():
    if not os.path.exists(TRADES_FILE):
        return []

    trades = []
    with open(TRADES_FILE, "r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        fields = set(reader.fieldnames or [])
        legacy_positions = not {"position_size_usdt", "leverage"}.intersection(fields)
        legacy_fees = "fee_rate" not in fields

        for row in reader:
            row.pop(None, None)

            if row.get("max_hold_minutes") in (None, ""):
                row["max_hold_minutes"] = str(MAX_HOLD_MINUTES)

            if row["status"] == "OPEN":
                if legacy_positions:
                    row["position_size_usdt"] = str(LEGACY_POSITION_SIZE_USDT)
                    row["leverage"] = str(LEGACY_LEVERAGE)
                if legacy_fees:
                    row["fee_rate"] = str(LEGACY_FEE_RATE)

            trades.append(row)

    return trades
def has_open_trade(symbol, open_trades):
    for trade in open_trades:
        if trade["symbol"] == symbol:
            return True

    return False

def is_symbol_in_cooldown(symbol, trades):
    now = datetime.now()
    cooldown_seconds = REENTRY_COOLDOWN_MINUTES * 60
    for trade in trades:
        if trade["symbol"] != symbol or trade["status"] != "CLOSED":
            continue
        try:
            closed_at = datetime.strptime(
                trade.get("closed_at"), "%Y-%m-%d %H:%M:%S"
            )
        except (TypeError, ValueError):
            print(symbol, "| invalid closed_at | new entries blocker")
            return True
        elapsed_seconds = (now - closed_at).total_seconds()
        if elapsed_seconds <0:
            print(symbol, "| closed_at is in the future | new entries blocked")
            return True
        if elapsed_seconds < cooldown_seconds:
           return True
    return False

def calculate_pnl_percent(side, entry_price, exit_price, leverage=None):
    if side not in {"BUY", "SELL"}:
        raise ValueError(f"Invalid side: {side}")

    entry_price = float(entry_price)
    exit_price = float(exit_price)
    leverage = float(LEVERAGE if leverage is None else leverage)

    if not all(
        isfinite(price) and price > 0
        for price in (entry_price, exit_price)
    ):
        raise ValueError("Prices must be finite and greater than zero")

    if not isfinite(leverage) or leverage <= 0:
        raise ValueError("Leverage must be finite and greater than zero")

    price_return = (exit_price - entry_price) / entry_price
    if side == "SELL":
        price_return = -price_return

    pnl_percent = price_return * leverage * 100
    if not isfinite(pnl_percent):
        raise ValueError("Calculated PnL is not finite")

    return pnl_percent


def calculate_pnl_usdt(pnl_percent, position_size_usdt=None):
    pnl_percent = float(pnl_percent)
    position_size_usdt = float(
        POZITION_SIZE_USDT if position_size_usdt is None else position_size_usdt
    )

    if not isfinite(pnl_percent):
        raise ValueError("PnL percent must be finite")

    if not isfinite(position_size_usdt) or position_size_usdt <= 0:
        raise ValueError("Position size must be finite and greater than zero")

    pnl_usdt = position_size_usdt * (pnl_percent / 100)
    if not isfinite(pnl_usdt):
        raise ValueError("Calculated USDT PnL is not finite")

    return pnl_usdt


def calculate_trade_fees_usdt(trade, exit_price):
    margin = float(trade["position_size_usdt"])
    leverage = float(trade["leverage"])
    entry_price = float(trade["entry_price"])
    exit_price = float(exit_price)
    fee_rate = float(trade["fee_rate"])

    values = (margin, leverage, entry_price, exit_price, fee_rate)
    if (
        not all(isfinite(value) for value in values)
        or min(margin, leverage, entry_price, exit_price) <= 0
        or not 0 <= fee_rate < 1
    ):
        raise ValueError("Invalid position parameters or fee rate")

    entry_notional = margin * leverage
    fees_usdt = entry_notional * fee_rate * (1 + exit_price / entry_price)

    if not isfinite(fees_usdt):
        raise ValueError("Calculated trading fees are not finite")

    return fees_usdt


def calculate_trade_pnl(trade, exit_price):
    gross_percent = calculate_pnl_percent(
        trade["side"],
        trade["entry_price"],
        exit_price,
        leverage=trade["leverage"]
    )
    margin = float(trade["position_size_usdt"])
    gross_usdt = calculate_pnl_usdt(gross_percent, margin)
    fees_usdt = calculate_trade_fees_usdt(trade, exit_price)

    net_usdt = gross_usdt - fees_usdt
    net_percent = net_usdt / margin * 100

    if not all(isfinite(value) for value in (net_usdt, net_percent)):
        raise ValueError("Calculated net PnL is not finite")

    return net_percent, net_usdt

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


def get_trade_hold_limit_minutes(trade):
    value = trade.get("max_hold_minutes", "")

    if value == "":
        return MAX_HOLD_MINUTES

    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return MAX_HOLD_MINUTES

def close_trade(trade, exit_price, reason):
    exit_price = float(exit_price)
    net_percent, net_usdt = calculate_trade_pnl(trade, exit_price)

    gross_percent = calculate_pnl_percent(
        trade["side"], trade["entry_price"], exit_price,
        leverage=trade["leverage"]
    )
    gross_usdt = calculate_pnl_usdt(
        gross_percent, trade["position_size_usdt"]
    )

    trade["entry_price"] = float(trade["entry_price"])
    trade["status"] = "CLOSED"
    trade["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    trade["exit_price"] = exit_price
    trade["pnl_percent"] = round(net_percent, 8)
    trade["pnl_usdt"] = round(net_usdt, 8)
    trade["gross_pnl_percent"] = round(gross_percent, 8)
    trade["gross_pnl_usdt"] = round(gross_usdt, 8)
    trade["fees_usdt"] = round(gross_usdt - net_usdt, 8)
    trade["close_reason"] = reason

    return net_percent

def save_all_trades(trades):
    fieldnames = [
        "opened_at", "symbol", "side", "entry_price", "confidence",
        "status", "closed_at", "exit_price", "pnl_percent", "pnl_usdt",
        "close_reason", "max_hold_minutes", "position_size_usdt", "leverage",
        "fee_rate", "gross_pnl_percent", "gross_pnl_usdt", "fees_usdt"
    ]
    directory = os.path.dirname(os.path.abspath(TRADES_FILE))
    temp_path = None

    try:
        with NamedTemporaryFile(
            mode="w", newline="", encoding="utf-8",
            dir=directory, prefix="paper_trades_", suffix=".tmp", delete=False
        ) as file:
            temp_path = file.name
            writer = csv.DictWriter(
                file, fieldnames=fieldnames, extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(trades)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_path, TRADES_FILE)
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.remove(temp_path)

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

def get_open_pnl_usdt():
    open_pnl_usdt = 0.0

    for trade in load_trades():
        if trade["status"] != "OPEN":
            continue

        current_price = get_price(trade["symbol"])
        _, current_pnl_usdt = calculate_trade_pnl(trade, current_price)
        open_pnl_usdt += current_pnl_usdt

    if not isfinite(open_pnl_usdt):
        raise ValueError("Total open PnL is not finite")

    return open_pnl_usdt

def calculate_paper_account_state(trades, open_pnl_usdt):
    initial = float(PAPER_INITIAL_BALANCE_USDT)
    open_pnl = float(open_pnl_usdt)

    if not isfinite(initial) or initial <= 0 or not isfinite(open_pnl):
        raise ValueError("Invalid paper capital or open PnL")

    closed_pnl = 0.0
    used_margin = 0.0

    for trade in trades:
        if trade["status"] == "CLOSED":
            pnl = float(trade["pnl_usdt"])
            if not isfinite(pnl):
                raise ValueError("Invalid closed PnL for paper account")
            closed_pnl += pnl

        elif trade["status"] == "OPEN":
            margin = float(trade["position_size_usdt"])
            if not isfinite(margin) or margin <= 0:
                raise ValueError("Invalid open position margin")
            used_margin += margin

        else:
            raise ValueError("Unknown paper trade status")

    balance = initial + closed_pnl
    equity = balance + open_pnl
    free_margin = equity - used_margin
    return_pct = (equity - initial) / initial * 100

    if not all(isfinite(value) for value in (
        closed_pnl, used_margin, balance, equity, free_margin, return_pct
    )):
        raise ValueError("Non-finite paper account state")

    return {
        "initial_balance_usdt": initial,
        "closed_pnl_usdt": closed_pnl,
        "balance_usdt": balance,
        "equity_usdt": equity,
        "used_margin_usdt": used_margin,
        "free_margin_usdt": free_margin,
        "return_pct": return_pct
    }

def update_paper_drawdown_state(account):
    initial = float(account["initial_balance_usdt"])
    equity = float(account["equity_usdt"])
    limit = float(PAPER_MAX_DRAWDOWN_PERCENT)

    if (
        not all(isfinite(value) for value in (initial, equity, limit))
        or initial <= 0
        or not 0 < limit < 100
    ):
        raise ValueError("Invalid paper equity or drawdown limit")

    with open(PAPER_ACCOUNT_STATE_FILE, "r", encoding="utf-8-sig") as file:
        state = json.load(file)

    if (
        not isinstance(state, dict)
        or type(state.get("version")) is not int
        or state["version"] != 1
        or type(state.get("entries_blocked")) is not bool
    ):
        raise ValueError("Invalid paper drawdown state")

    values = (
        state["initial_balance_usdt"],
        state["peak_equity_usdt"],
        state["max_drawdown_pct"]
    )
    if any(isinstance(value, bool) for value in values):
        raise ValueError("Invalid numeric drawdown state")

    stored_initial, peak, max_drawdown = map(float, values)
    if (
        not all(isfinite(value) for value in (stored_initial, peak, max_drawdown))
        or stored_initial != initial
        or peak < initial
        or max_drawdown < 0
    ):
        raise ValueError("Invalid paper drawdown history")

    peak = max(peak, equity)
    equity_floor = peak * (1 - limit / 100)
    drawdown = max(0.0, (peak - equity) / peak * 100)

    if not all(isfinite(value) for value in (equity_floor, drawdown)):
        raise ValueError("Non-finite drawdown calculation")

    max_drawdown = max(max_drawdown, drawdown)
    state = {
        "version": 1,
        "initial_balance_usdt": initial,
        "peak_equity_usdt": peak,
        "max_drawdown_pct": max_drawdown,
        "entries_blocked": (
            state["entries_blocked"]
            or equity <= equity_floor
            or max_drawdown >= limit
        )
    }

    directory = os.path.dirname(os.path.abspath(PAPER_ACCOUNT_STATE_FILE))
    temp_path = None

    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory,
            prefix="paper_drawdown_", suffix=".tmp", delete=False
        ) as file:
            temp_path = file.name
            json.dump(state, file, ensure_ascii=False, allow_nan=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_path, PAPER_ACCOUNT_STATE_FILE)

    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.remove(temp_path)

    return {
        **state,
        "drawdown_pct": drawdown,
        "equity_floor_usdt": equity_floor
    }

def print_portfolio_summary():
    trades = load_trades()

    open_trades = [trade for trade in trades if trade["status"] == "OPEN"]
    closed_trades = [trade for trade in trades if trade["status"] == "CLOSED"]

    open_pnl = 0
    open_pnl_usdt = 0

    for trade in open_trades:
        symbol = trade["symbol"]
        current_price = get_price(symbol)

        current_pnl, current_pnl_usdt = calculate_trade_pnl(
            trade, current_price
        )

        open_pnl += current_pnl
        open_pnl_usdt += current_pnl_usdt


    closed_pnl = 0
    closed_pnl_usdt = 0

    for trade in closed_trades:
        if trade["pnl_percent"] == "":
            continue

        closed_pnl += float(trade["pnl_percent"])
        
        if "pnl_usdt" in trade and trade["pnl_usdt"] != "":
            closed_pnl_usdt += float(trade["pnl_usdt"])

    total_pnl = open_pnl + closed_pnl
    account = calculate_paper_account_state(trades, open_pnl_usdt)
    drawdown = update_paper_drawdown_state(account)
    closed_pnl_usdt = account["closed_pnl_usdt"]
    total_pnl_usdt = closed_pnl_usdt + open_pnl_usdt
    
    today_closed_pnl_usdt = get_today_closed_pnl_usdt()
    daily_risk_pnl_usdt = today_closed_pnl_usdt + open_pnl_usdt

    if (
        not isfinite(daily_risk_pnl_usdt)
        or daily_risk_pnl_usdt <= MAXDAILY_LOSS_USDT
    ):
        daily_risk_status = "BLOCKED"
    else:
        daily_risk_status = "ACTIV"


    print()
    print("PORTFOLIO SUMMARY")
    print("open trades:", len(open_trades))
    print("closed trades:", len(closed_trades))
    print("sum of open trade pnl:", round(open_pnl, 4), "%")
    print("open pnl_usdt:", round(open_pnl_usdt, 4), "USDT")
    print("sum of closed trade pnl:", round(closed_pnl, 4), "%")
    print("closed pnl_usdt:", round(closed_pnl_usdt, 4), "USDT")
    print("sum of all trade pnl:", round(total_pnl, 4), "%")
    print("total pnl_usdt:", round(total_pnl_usdt, 4), "USDT")
    print("daily closed pnl_usdt:", round(today_closed_pnl_usdt, 4), "USDT")
    print("daily risk pnl_usdt:", round(daily_risk_pnl_usdt, 4), "USDT")
    print("daily loss limit:", MAXDAILY_LOSS_USDT, "USDT")
    print("daily risk status:", daily_risk_status)
    print("paper balance_usdt:", round(account["balance_usdt"], 4), "USDT")
    print("estimated equity_usdt:", round(account["equity_usdt"], 4), "USDT")
    print("used margin_usdt:", round(account["used_margin_usdt"], 4), "USDT")
    print("estimated free margin_usdt:", round(account["free_margin_usdt"], 4), "USDT")
    print("paper account return:", round(account["return_pct"], 4), "%")
    print("peak equity_usdt:", round(drawdown["peak_equity_usdt"], 4), "USDT")
    print("drawdown:", round(drawdown["drawdown_pct"], 4), "%")
    print("max observed drawdown:", round(drawdown["max_drawdown_pct"], 4), "%")
    print("drawdown limit:", PAPER_MAX_DRAWDOWN_PERCENT, "%")
    print(
        "drawdown entry status:",
        "BLOCKED" if drawdown["entries_blocked"] else "ACTIVE"
    )
    
def append_trade(trade):
    calculate_trade_pnl(trade, trade["entry_price"])
    trades = load_trades()
    trades.append(trade)
    save_all_trades(trades)

POSITION_CHECKS_FILE = "position_checks.csv"

POSITION_CHECK_FIELDS = (
    "checked_at_utc", "price_received_at_utc", "opened_at", "symbol", "side",
    "entry_price", "previous_price", "current_price", "stop_loss_price",
    "check_gap_s", "since_good_price_s", "request_ms", "sl_overrun_pct",
    "price_status", "error_type", "trade_status", "close_reason"
)

_last_position_check = {}


def get_price_with_check_log(trade, records):
    started = monotonic()
    checked_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    price = None
    error_type = ""

    try:
        price = get_price(trade["symbol"])
        return price

    except Exception as error:
        error_type = type(error).__name__
        raise

    finally:
        finished = monotonic()

        try:
            key = (trade["opened_at"], trade["symbol"], trade["side"])
            previous = _last_position_check.get(key, {})
            entry = float(trade["entry_price"])
            stop_rate = float(STOP_LOSS)

            if (
                not isfinite(entry) or entry <= 0
                or not isfinite(stop_rate) or not 0 < stop_rate < 1
                or trade["side"] not in {"BUY", "SELL"}
            ):
                raise ValueError("Invalid position audit metadata")

            direction = 1 if trade["side"] == "BUY" else -1
            stop_price = entry * (1 - direction * stop_rate)
            valid_price = price is not None and isfinite(price) and price > 0

            row = {
                "checked_at_utc": checked_at,
                "price_received_at_utc": (
                    datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                    if not error_type else ""
                ),
                "opened_at": trade["opened_at"],
                "symbol": trade["symbol"],
                "side": trade["side"],
                "entry_price": entry,
                "previous_price": previous.get("price", ""),
                "current_price": price if valid_price else "",
                "stop_loss_price": stop_price,
                "check_gap_s": (
                    round(started - previous["attempt"], 3)
                    if "attempt" in previous else ""
                ),
                "since_good_price_s": (
                    round(finished - previous["good"], 3)
                    if "good" in previous else ""
                ),
                "request_ms": round((finished - started) * 1000, 3),
                "sl_overrun_pct": (
                    round(
                        max(0, direction * (stop_price - price) / entry * 100),
                        8
                    )
                    if valid_price else ""
                ),
                "price_status": (
                    "ERROR" if error_type
                    else "OK" if valid_price
                    else "INVALID"
                ),
                "error_type": error_type
            }

            previous["attempt"] = started

            if valid_price:
                previous.update(good=finished, price=price)

            _last_position_check[key] = previous
            records.append((trade, row))

        except Exception as log_error:
            print(
                "Position check capture failed:",
                type(log_error).__name__,
                flush=True
            )


def append_position_checks(records):
    if not records:
        return

    try:
        rows = []

        for trade, row in records:
            row["trade_status"] = trade["status"]
            row["close_reason"] = trade.get("close_reason", "")
            rows.append(row)

            if trade["status"] != "OPEN":
                key = (row["opened_at"], row["symbol"], row["side"])
                _last_position_check.pop(key, None)

        with open(
            POSITION_CHECKS_FILE, "a", newline="", encoding="utf-8"
        ) as file:
            writer = csv.DictWriter(file, fieldnames=POSITION_CHECK_FIELDS)

            if file.tell() == 0:
                writer.writeheader()

            writer.writerows(rows)

    except Exception as log_error:
        print(
            "Position check log write failed:",
            type(log_error).__name__,
            flush=True
        )

def manage_open_trades(trades, selected_symbols, latest_signals_map):
    open_trades = [
        trade for trade in trades
        if trade["status"] == "OPEN"
     ]

    if not open_trades:
        return

    failed_symbols = set()
    position_checks = []

    for trade in open_trades:
        symbol = trade["symbol"]

        try:
            current_price = get_price_with_check_log(
                trade, position_checks
            )
            current_pnl, current_pnl_usdt = calculate_trade_pnl(
                trade, current_price
            )

            gross_pnl = calculate_pnl_percent(
                trade["side"],
                trade["entry_price"],
                current_price,
                leverage=trade["leverage"]
            )

            trade_age_minutes = calculate_trade_age_minutes(
                trade["opened_at"]
            )

        except Exception as error:
            failed_symbols.add(symbol)
            print(
                symbol,
                "| manage | position data unavailable |",
                error
            )
            continue

        # Convert price-based targets to leveraged PnL percent.
        trade_leverage = float(trade["leverage"])
        take_profit_pnl = TAKE_PROFIT * trade_leverage * 100
        stop_loss_pnl = STOP_LOSS * trade_leverage * 100

        if gross_pnl >= take_profit_pnl:
            close_trade(trade, current_price, "TAKE_PROFIT")
            print(symbol, "| manage | closed by take profit |", current_price)
            continue

        if gross_pnl <= -stop_loss_pnl:
            close_trade(trade, current_price, "STOP_LOSS")
            print(symbol, "| manage | closed by stop loss |", current_price)
            continue

        signal = latest_signals_map.get(symbol, "HOLD")
        if signal in {"BUY", "SELL"} and trade["side"] != signal:
            close_trade(trade, current_price, "OPPOSITE_SIGNAL")
            print(symbol, "| manage | closed by opposite signal |", current_price)
            continue

        hold_limit_minutes = get_trade_hold_limit_minutes(trade)
        hard_hold_limit_minutes = MAX_HOLD_MINUTES + HOLD_EXTENSION_MINUTES

        if trade_age_minutes >= hard_hold_limit_minutes:
            close_trade(trade, current_price, "MAX_HOLD_TIME")
            print(symbol, "| manage | closed by maximum hold time |", current_price)
            continue

        if trade_age_minutes >= hold_limit_minutes:
            if current_pnl_usdt > 0:
                close_trade(trade, current_price, "TIME_EXIT_IN_PROFIT")
                print(symbol, "| manage | closed by time (profit) |", current_price)
            else:
                trade["max_hold_minutes"] = str(hard_hold_limit_minutes)
                print(
                    symbol,
                    "| manage | extended hold, total limit:",
                    f"{hard_hold_limit_minutes}m"
                )

    save_all_trades(trades)
    append_position_checks(position_checks)

    if failed_symbols:
        raise RuntimeError(
            "Position valuation failed; new entries blocked for: "
            + ", ".join(sorted(failed_symbols))
        )
    
def calculate_position_size_usdt(
    open_trades,
    today_closed_pnl_usdt,
    *,
    sizing_state=None,
    leverage=None,
    stop_loss=None,
    fee_rate=None,
):
    leverage = float(LEVERAGE if leverage is None else leverage)
    stop_loss = float(STOP_LOSS if stop_loss is None else stop_loss)
    fee_rate = float(COMMISSION if fee_rate is None else fee_rate)
    max_margin = float(POZITION_SIZE_USDT)
    per_trade_risk = float(MAX_RISK_PER_TRADE_USDT)
    max_open_risk = float(MAX_TOTAL_OPEN_RISK_USDT)
    max_notional = float(MAX_TOTAL_ENTRY_NOTIONAL_USDT)
    max_leverage = float(MAX_ALLOWED_LEVERAGE)
    daily_limit = float(MAXDAILY_LOSS_USDT)
    closed_pnl = float(today_closed_pnl_usdt)

    values = (
        leverage, stop_loss, fee_rate, max_margin, per_trade_risk,
        max_open_risk, max_notional, max_leverage, daily_limit, closed_pnl
    )
    if not all(isfinite(value) for value in values):
        raise ValueError("Risk settings and closed PnL must be finite")

    if not 1 <= leverage <= max_leverage:
        raise ValueError("New position leverage exceeds the allowed range")

    if not 0 < stop_loss < 1 or not 0 <= fee_rate < 1 or daily_limit >= 0:
        raise ValueError("Invalid stop loss, fee rate or daily limit")

    if min(max_margin, per_trade_risk, max_open_risk, max_notional) <= 0:
        raise ValueError("Risk, margin and notional limits must be positive")

    open_entry_notional = 0.0
    open_risk = 0.0

    for trade in open_trades:
        if trade["status"] != "OPEN":
            continue

        margin = float(trade["position_size_usdt"])
        trade_leverage = float(trade["leverage"])
        trade_fee = float(trade["fee_rate"])

        if (
            not all(isfinite(value) for value in (margin, trade_leverage, trade_fee))
            or min(margin, trade_leverage) <= 0
            or not 0 <= trade_fee < 1
        ):
            raise ValueError(f"Invalid position parameters: {trade['symbol']}")

        notional = margin * trade_leverage
        open_entry_notional += notional
        open_risk += notional * (stop_loss + trade_fee * (2 + stop_loss))

    if not all(isfinite(value) for value in (open_entry_notional, open_risk)):
        raise ValueError("Total open notional or risk is not finite")

    risk_budget = min(
        per_trade_risk,
        max_open_risk - open_risk,
        closed_pnl - daily_limit - open_risk
    )
    notional_budget = max_notional - open_entry_notional

    if risk_budget <= 0 or notional_budget <= 0:
        return 0.0

    loss_per_margin = leverage * (stop_loss + fee_rate * (2 + stop_loss))

    if sizing_state is None:
        account = calculate_paper_account_state(
            load_trades(), get_open_pnl_usdt()
        )
        drawdown = update_paper_drawdown_state(account)
        sizing_state = {
            "balance_usdt": account["balance_usdt"],
            "free_margin_usdt": account["free_margin_usdt"],
            "equity_floor_usdt": drawdown["equity_floor_usdt"],
            "entries_blocked": drawdown["entries_blocked"],
        }

    balance = float(sizing_state["balance_usdt"])
    free_margin = float(sizing_state["free_margin_usdt"])
    equity_floor = float(sizing_state["equity_floor_usdt"])
    entries_blocked = sizing_state["entries_blocked"]

    if (
        type(entries_blocked) is not bool
        or not all(isfinite(value) for value in (balance, free_margin, equity_floor))
        or equity_floor <= 0
    ):
        raise ValueError("Invalid position sizing state")

    if entries_blocked:
        return 0.0

    drawdown_budget = balance - equity_floor - open_risk
    risk_budget = min(risk_budget, drawdown_budget)

    if risk_budget <= 0 or free_margin <= 0:
        return 0.0

    capital_margin_limit = free_margin / (1 + 2 * leverage * fee_rate)

    margin = min(
        max_margin,
        risk_budget / loss_per_margin,
        notional_budget / leverage,
        capital_margin_limit
    )
    return floor(margin * 100) / 100

def main():
    try:
        latest_signals = load_latest_signals()
        selected_symbols = load_selected_symbols()
    except (OSError, csv.Error, KeyError, ValueError) as error:
        print(
            "Signal input unavailable | new entries blocked |",
            type(error).__name__,
            error,
            flush=True,
        )
        latest_signals = []
        selected_symbols = []
    

    if not selected_symbols:
        print("No selected symbols for new paper trades.")
    latest_signals = [
        item for item in latest_signals
        if item["symbol"] in selected_symbols
    ]
    latest_signals_map = {
        item["symbol"]: item["signal"] for item in latest_signals
    }
    trades = load_trades()
    manage_open_trades(trades, selected_symbols, latest_signals_map)
    trades = load_trades()
    open_trades = [
        trade for trade in trades
        if trade["status"] == "OPEN"
     ]

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
        
        open_trades = [
            trade for trade in load_trades()
            if trade["status"] == "OPEN"
         ]

        if has_open_trade(symbol, open_trades):

            for trade in open_trades:
                if trade["symbol"] == symbol:
                    current_price = get_price(symbol)

                    current_pnl, current_pnl_usdt = calculate_trade_pnl(
                        trade, current_price
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
        
        open_trades = [
            trade for trade in load_trades()
            if trade["status"] == "OPEN"
         ]
        
        if len(open_trades) >= MAX_OPEN_TRADES:
            print(symbol, "|", signal, "| skipped max open trades | -")
            continue
        
        today_closed_pnl_usdt = get_today_closed_pnl_usdt()
        today_pnl_usdt = today_closed_pnl_usdt + get_open_pnl_usdt()
        if (
            not isfinite(today_pnl_usdt)
            or today_pnl_usdt <= MAXDAILY_LOSS_USDT
        ):
            print(
                symbol,
                "|",
                signal,
                "| skipped daily loss limit |",
                round(today_pnl_usdt, 4),
                "USDT"
           )
            continue

        if is_symbol_in_cooldown(symbol, trades):
            print(symbol, "|", signal, "| skipped re-entry cooldown | -")
            continue
        position_size_usdt = calculate_position_size_usdt(
            open_trades, today_closed_pnl_usdt
        )

        if position_size_usdt <= 0:
            print(
                symbol,
                "|",
                signal,
                "| skipped entry risk or notional limit | -"
            )
            continue

        try:
            price = get_entry_price(
                symbol,
                max_spread_percent=MAX_ENTRY_SPREAD_PERCENT,
                min_turnover_24h_usdt=MIN_ENTRY_TURNOVER_24H_USDT
            )
            if not isfinite(price) or price <= 0:
                raise ValueError(f"Invalid entry price for {symbol}: {price!r}")

        except Exception as error:
            print(
                symbol,
                "|",
                signal,
                "| skipped entry market check |",
                type(error).__name__,
                str(error)
            )
            continue
        signal_time = datetime.strptime(
            item["timestamp"], "%Y-%m-%d %H:%M:%S"
        )
        signal_age_seconds = (datetime.now() - signal_time).total_seconds()

        if not 0 <= signal_age_seconds <= MAX_SIGNAL_AGE_MINUTES * 60:
            print(symbol, "|", signal, "| skipped stale signal |-")
            continue

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
            "close_reason": "",
            "max_hold_minutes": str(MAX_HOLD_MINUTES),
            "position_size_usdt": position_size_usdt,
            "leverage": LEVERAGE,
            "fee_rate": float(COMMISSION)
        }

        append_trade(trade)

        print(
            symbol,
            "|",
            signal,
            "| opened paper trade |",
            price,
            "| margin:",
            position_size_usdt,
            "USDT"
        )

    print()
    print("OPEN TRADES SUMMARY")
    print("symbol | side | entry | current | pnl | pnl USDT | age | status")

    updated_trades = load_trades()
    updated_open_trades = [trade for trade in updated_trades if trade["status"] == "OPEN"]

    for trade in updated_open_trades:
        symbol = trade["symbol"]
        current_price = get_price(symbol)

        current_pnl, current_pnl_usdt = calculate_trade_pnl(
            trade, current_price
        )
        
        trade_age = calculate_trade_age(trade["opened_at"])

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
    

def _paper_writer_lock():
    return exclusive_file_lock(TRADES_FILE, "Paper writer")

def _initialize_paper_account():
    state_path = os.path.abspath(PAPER_ACCOUNT_STATE_FILE)
    if os.path.lexists(state_path):
        raise SystemExit(f"Paper account state already exists: {state_path}")
    if os.path.lexists(TRADES_FILE):
        raise SystemExit("Paper journal already exists. Restore its account state from backup.")

    initial = float(PAPER_INITIAL_BALANCE_USDT)
    if not isfinite(initial) or initial <= 0:
        raise ValueError("Invalid paper initial balance")

    state = {
        "version": 1,
        "initial_balance_usdt": initial,
        "peak_equity_usdt": initial,
        "max_drawdown_pct": 0.0,
        "entries_blocked": False,
    }
    temp_path = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8",
            dir=os.path.dirname(state_path),
            prefix="paper_init_", suffix=".tmp", delete=False,
        ) as file:
            temp_path = file.name
            json.dump(state, file, allow_nan=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        if os.name == "nt":
            os.rename(temp_path, state_path)
        else:
            os.link(temp_path, state_path)
    except FileExistsError:
        raise SystemExit(f"Paper account state already exists: {state_path}") from None
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.remove(temp_path)

    print("Initialized paper account state:", state_path)

def _write_paper_status(status, error_type=""):
    directory = os.path.dirname(os.path.abspath(TRADES_FILE))
    status_path = os.path.join(directory, "paper_status.json")
    temp_path = None
    try:
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=directory,
                prefix="paper_status_", suffix=".tmp", delete=False,
            ) as file:
                temp_path = file.name
                json.dump(
                    {
                        "status": status,
                        "updated_at": time(),
                        "error_type": error_type,
                        "pid": os.getpid(),
                        "run_id": os.environ.get("IMPERATOR_PAPER_RUN_ID"),
                    },
                    file,
                    allow_nan=False,
                )
            os.replace(temp_path, status_path)
        finally:
            if temp_path is not None and os.path.exists(temp_path):
                os.remove(temp_path)
    except OSError as error:
        print("paper status write failed:", type(error).__name__, flush=True)

if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument(
        "--init-account",
        action="store_true",
        help="Initialize an account in a fresh paper directory",
    )
    args = parser.parse_args()

    try:
        with _paper_writer_lock():
            if args.init_account:
                _initialize_paper_account()
                raise SystemExit(0)

            try:
                _write_paper_status("STARTING")

                if not os.path.lexists(PAPER_ACCOUNT_STATE_FILE):
                    if not os.path.lexists(TRADES_FILE):
                        raise SystemExit(
                            "Paper account is uninitialized. Run python paper_trading.py --init-account."
                        )
                    print(
                        "Paper account state is missing. Restore it before opening new positions.",
                        flush=True,
                    )

                while True:
                    print(
                        "\nPAPER CYCLE",
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        flush=True
                    )

                    try:
                        main()
                    except Exception as error:
                        print(
                            "paper cycle failed:",
                            type(error).__name__,
                            str(error),
                            flush=True
                        )
                        _write_paper_status("ERROR", type(error).__name__)
                    else:
                        _write_paper_status("OK")

                    sleep(PAPER_CHECK_INTERVAL_SECONDS)
            finally:
                _write_paper_status("STOPPED")

    except KeyboardInterrupt:
        print("\nPaper trading stopped.")