from datetime import date
import paper_trading as paper
from data import get_klines
from indicators import ema, rsi, momentum, average_volume, volatility, market_regime
from settings import (
    STOP_LOSS,
    TAKE_PROFIT,
    LEVERAGE,
    COMMISSION,
    MAX_HOLD_MINUTES,
    HOLD_EXTENSION_MINUTES,
    CONFIDENCE_LEVELS,
    STOP_LOSS_GRID,
    TAKE_PROFIT_GRID
)
from features import (
    normalize_rsi,
    normalize_momentum,
    normalize_volume,
    normalize_volatility
)
from model import train_model, predict_signal, show_feature_importance
from math import isfinite
PREDICTION_HORIZON = 5


def prepare_training_data(X, y, train_end):
    if len(X) != len(y):
        raise ValueError("Feature and label counts must match")

    if not isinstance(train_end, int) or not 0 <= train_end <= len(X):
        raise ValueError("Invalid training boundary")

    safe_train_end = max(0, train_end - PREDICTION_HORIZON)

    X_train = []
    y_train = []

    for features, label in zip(X[:safe_train_end], y[:safe_train_end]):
        if label is None:
            continue
        if label not in (0, 1):
            raise ValueError("Training labels must be 0, 1 or None")

        X_train.append(features)
        y_train.append(label)

    return X_train, y_train
def calculate_net_trade_return(side, entry_price, exit_price, leverage, commission):
    if side not in {"BUY", "SELL"}:
        raise ValueError(f"Invalid side: {side}")

    entry_price = float(entry_price)
    exit_price = float(exit_price)
    leverage = float(leverage)
    commission = float(commission)

    values = (entry_price, exit_price, leverage, commission)
    if (
        not all(isfinite(value) for value in values)
        or min(entry_price, exit_price, leverage) <= 0
        or not 0 <= commission < 1
    ):
        raise ValueError("Invalid prices, leverage or commission")

    ratio = exit_price / entry_price
    direction = 1 if side == "BUY" else -1
    net_return = leverage * (
        direction * (ratio - 1) - commission * (1 + ratio)
    )

    if not isfinite(net_return):
        raise ValueError("Calculated net return is not finite")

    return net_return


def _simulate_trade_execution(
    side, entry_price, future_candles,
    stop_loss, take_profit, leverage, commission,
    *, signals_at_open=(),
):
    if side not in {"BUY", "SELL"} or not future_candles:
        raise ValueError("Valid side and non-empty future candles required")

    entry_price = float(entry_price)
    stop_loss = float(stop_loss)
    take_profit = float(take_profit)

    if (
        not all(isfinite(value) for value in (entry_price, stop_loss, take_profit))
        or entry_price <= 0
        or not 0 < stop_loss < 1
        or not 0 < take_profit < 1
    ):
        raise ValueError("Invalid entry price or TP/SL")

    stop_price = entry_price * (1 - stop_loss if side == "BUY" else 1 + stop_loss)
    take_price = entry_price * (1 + take_profit if side == "BUY" else 1 - take_profit)
    lower_exit_price = min(stop_price, take_price)
    upper_exit_price = max(stop_price, take_price)

    for bars_held, candle in enumerate(future_candles, start=1):
        open_price = float(candle["open"]) if "open" in candle else None
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])

        if (
            not all(isfinite(value) for value in (high, low, close))
            or low <= 0
            or not low <= close <= high
            or (open_price is not None and not low <= open_price <= high)
        ):
            raise ValueError("Invalid future candle")

        signal_at_open = (
            signals_at_open[bars_held - 1]
            if bars_held <= len(signals_at_open)
            else "HOLD"
        )
        opposite_signal = signal_at_open in {"BUY", "SELL"} and signal_at_open != side

        exit_price = None
        exit_phase = "INTRABAR"

        if open_price is not None and (
            opposite_signal
            or open_price <= lower_exit_price
            or open_price >= upper_exit_price
        ):
            exit_price = open_price
            exit_phase = "OPEN"
        elif side == "BUY":
            if low <= stop_price:
                exit_price = stop_price
            elif high >= take_price:
                exit_price = take_price
        else:
            if high >= stop_price:
                exit_price = stop_price
            elif low <= take_price:
                exit_price = take_price

        if exit_price is not None:
            net_return = calculate_net_trade_return(
                side, entry_price, exit_price, leverage, commission
            )
            break

        candle_minutes = 60
        minutes_held = bars_held * candle_minutes
        first_time_check = (
            minutes_held - candle_minutes < MAX_HOLD_MINUTES <= minutes_held
        )
        hard_limit_reached = (
            minutes_held >= MAX_HOLD_MINUTES + HOLD_EXTENSION_MINUTES
        )

        if first_time_check or hard_limit_reached:
            net_return = calculate_net_trade_return(
                side, entry_price, close, leverage, commission
            )
            if hard_limit_reached or net_return > 0:
                exit_price = close
                exit_phase = "CLOSE"
                break
    else:
        exit_price = close
        exit_phase = "CLOSE"
        net_return = calculate_net_trade_return(
            side, entry_price, exit_price, leverage, commission
        )

    return {
        "side": side,
        "entry_time_ms": future_candles[0].get("timestamp"),
        "entry_price": entry_price,
        "exit_bar_start_ms": future_candles[bars_held - 1].get("timestamp"),
        "exit_price": exit_price,
        "exit_phase": exit_phase,
        "bars_held": bars_held,
        "net_return": net_return,
    }


def simulate_trade_with_exit(
    side, entry_price, future_candles,
    stop_loss, take_profit, leverage, commission,
    *, signals_at_open=(),
):
    execution = _simulate_trade_execution(
        side, entry_price, future_candles,
        stop_loss, take_profit, leverage, commission,
        signals_at_open=signals_at_open,
    )
    return execution["net_return"], execution["bars_held"]


def simulate_trade(
    side, entry_price, future_candles,
    stop_loss, take_profit, leverage, commission
):
    net_return, _ = simulate_trade_with_exit(
        side, entry_price, future_candles,
        stop_loss, take_profit, leverage, commission
    )
    return net_return
def summarize_returns(returns):
    returns = [float(value) for value in returns]
    if any(not isfinite(value) or value <= -1 for value in returns):
        raise ValueError("Invalid return for the model equity curve")

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0

    for value in returns:
        equity *= 1 + value
        if not isfinite(equity):
            raise ValueError("Model equity is not finite")
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)

    trades = len(returns)
    wins = sum(value > 0 for value in returns)
    gains = sum(value for value in returns if value > 0)
    losses_sum = -sum(value for value in returns if value < 0)
    profit_factor = (
        gains / losses_sum if losses_sum > 0
        else float("inf") if gains > 0 else 0.0
    )

    return {
        "trades": trades,
        "wins": wins,
        "losses": trades - wins,
        "winrate": wins / trades * 100 if trades else 0.0,
        "avg_return": sum(returns) / trades * 100 if trades else 0.0,
        "total_return": (equity - 1) * 100,
        "max_drawdown": max_drawdown * 100,
        "profit_factor": profit_factor,
        "returns": returns
    }
class _PaperAccountReplay:
    def __init__(self, stop_loss, leverage, commission):
        self.initial_balance = float(paper.PAPER_INITIAL_BALANCE_USDT)
        self.drawdown_limit = float(paper.PAPER_MAX_DRAWDOWN_PERCENT)
        if (
            not isfinite(self.initial_balance)
            or self.initial_balance <= 0
            or not 0 < self.drawdown_limit < 100
        ):
            raise ValueError("Invalid paper account limits")

        self.balance = self.peak_equity = self.initial_balance
        self.max_drawdown_pct = 0.0
        self.entries_blocked = False
        self.daily_pnl = {}
        self.trades = 0
        self.skipped_signals = 0
        self.stop_loss = stop_loss
        self.leverage = leverage
        self.commission = commission

    @staticmethod
    def _day(timestamp_ms, *, at_close=False):
        if type(timestamp_ms) is not int or timestamp_ms <= 0:
            raise ValueError("Paper replay requires candle timestamps in milliseconds")
        if at_close:
            timestamp_ms += 3_600_000
        return date.fromtimestamp(timestamp_ms / 1000)

    def _equity_floor(self):
        return self.peak_equity * (1 - self.drawdown_limit / 100)

    def _observe_equity(self, equity):
        if not isfinite(equity):
            raise ValueError("Non-finite replay equity")
        self.peak_equity = max(self.peak_equity, equity)
        drawdown = (self.peak_equity - equity) / self.peak_equity * 100
        if not isfinite(drawdown):
            raise ValueError("Non-finite replay drawdown")
        self.max_drawdown_pct = max(self.max_drawdown_pct, drawdown)
        self.entries_blocked = (
            self.entries_blocked
            or equity <= self._equity_floor()
            or self.max_drawdown_pct >= self.drawdown_limit
        )

    def position_size_usdt(self, timestamp_ms):
        daily_pnl = self.daily_pnl.get(self._day(timestamp_ms), 0.0)
        margin = paper.calculate_position_size_usdt(
            [],
            daily_pnl,
            sizing_state={
                "balance_usdt": self.balance,
                "free_margin_usdt": self.balance,
                "equity_floor_usdt": self._equity_floor(),
                "entries_blocked": self.entries_blocked,
            },
            leverage=self.leverage,
            stop_loss=self.stop_loss,
            fee_rate=self.commission,
        )
        if margin <= 0:
            self.skipped_signals += 1
        return margin

    def record_trade(self, execution, future_candles, margin):
        exit_day = self._day(
            execution["exit_bar_start_ms"],
            at_close=execution["exit_phase"] == "CLOSE",
        )
        entry_price = execution["entry_price"]
        mark_prices = [entry_price]
        mark_prices.extend(
            candle["close"] for candle in future_candles[:execution["bars_held"] - 1]
        )
        for mark_price in mark_prices:
            net_return = calculate_net_trade_return(
                execution["side"], entry_price, mark_price, self.leverage, self.commission
            )
            self._observe_equity(self.balance + margin * net_return)

        pnl_usdt = round(margin * execution["net_return"], 8)
        self.balance += pnl_usdt
        self.daily_pnl[exit_day] = self.daily_pnl.get(exit_day, 0.0) + pnl_usdt
        self._observe_equity(self.balance)
        self.trades += 1

    def summary(self):
        return {
            "initial_balance_usdt": self.initial_balance,
            "balance_usdt": self.balance,
            "pnl_usdt": self.balance - self.initial_balance,
            "peak_equity_usdt": self.peak_equity,
            "max_drawdown_pct": self.max_drawdown_pct,
            "entries_blocked": self.entries_blocked,
            "trades": self.trades,
            "skipped_signals": self.skipped_signals,
        }
def run_test_window(
    X, y, prices, future_candles_list, train_end, test_end,
    confidence_levels, stop_loss, take_profit, leverage, commission,
    *, replay_account=False,
):
    if not len(X) == len(y) == len(prices) == len(future_candles_list):
        raise ValueError("Dataset columns must have equal lengths")
    if not 0 <= train_end <= test_end <= len(X):
        raise ValueError("Invalid temporal boundaries")

    def skipped(reason, validation_results=None):
        result = summarize_returns([])
        result.update({
            "confidence": None,
            "status": "SKIPPED",
            "reason": reason,
            "executions": [],
            "validation_results": validation_results or []
        })
        return result

    validation_start = int(train_end * 0.8)
    validation_end = train_end - PREDICTION_HORIZON
    test_stop = test_end - PREDICTION_HORIZON

    if validation_start >= validation_end or train_end >= test_stop:
        return skipped("Not enough rows for validation and test")

    X_fit, y_fit = prepare_training_data(X, y, validation_start)
    X_refit, y_refit = prepare_training_data(X, y, train_end)
    if len(set(y_fit)) < 2 or len(set(y_refit)) < 2:
        return skipped("Training needs both BUY and SELL classes")

    thresholds = [float(value) for value in confidence_levels]
    if any(not isfinite(value) or not 0.5 <= value < 1 for value in thresholds):
        raise ValueError("Confidence must be in [0.5, 1)")

    def evaluate(model, confidence, start, end, *, account=None):
        signal_end = end + PREDICTION_HORIZON
        signals = []

        for probabilities in model.predict_proba(X[start:signal_end]):
            sell, buy = map(float, probabilities)
            if (
                not all(isfinite(value) and 0 <= value <= 1 for value in (sell, buy))
                or abs(sell + buy - 1) > 1e-6
            ):
                raise ValueError("Invalid model probabilities")

            if buy >= confidence:
                signals.append("BUY")
            elif sell >= confidence:
                signals.append("SELL")
            else:
                signals.append("HOLD")

        executions = []
        index = start

        while index < end:
            signal_index = index - start
            side = signals[signal_index]
            if side == "HOLD":
                index += 1
                continue

            future_candles = future_candles_list[index]
            entry_price = prices[index]
            if future_candles:
                entry_price = future_candles[0].get("open", entry_price)

            if account is not None:
                if not future_candles:
                    raise ValueError("Paper replay requires future candles")
                margin = account.position_size_usdt(future_candles[0].get("timestamp"))
                if margin <= 0:
                    index += 1
                    continue

            execution = _simulate_trade_execution(
                side, entry_price, future_candles,
                stop_loss, take_profit, leverage, commission,
                signals_at_open=signals[signal_index:signal_index + PREDICTION_HORIZON],
            )
            if account is None:
                executions.append(execution)
            else:
                account.record_trade(execution, future_candles, margin)

            index += execution["bars_held"] + 1

        if account is not None:
            return account.summary()

        returns = [execution["net_return"] for execution in executions]
        result = summarize_returns(returns)
        result["confidence"] = confidence
        result["executions"] = executions
        return result
    
    validation_model = train_model(X_fit, y_fit)
    validation_results = [
        evaluate(validation_model, value, validation_start, validation_end)
        for value in thresholds
    ]
    candidates = [result for result in validation_results if result["trades"] >= 10]
    if not candidates:
        return skipped("No validation threshold has at least 10 trades", validation_results)

    selected = max(
        candidates,
        key=lambda result: (
            result["profit_factor"], result["avg_return"], -result["confidence"]
        )
    )

    test_model = train_model(X_refit, y_refit)
    result = evaluate(test_model, selected["confidence"], train_end, test_stop)
    if replay_account:
        result["paper_account"] = evaluate(
            test_model,
            selected["confidence"],
            train_end,
            test_stop,
            account=_PaperAccountReplay(stop_loss, leverage, commission),
        )
    passed = (
        result["trades"] >= 10
        and result["profit_factor"] >= 1.1
        and result["total_return"] > 0
        and result["max_drawdown"] <= 20
    )
    result.update({
        "status": "PASSED" if passed else "FAILED",
        "validation_results": validation_results
    })
    return result
def run_walk_forward(
    X, y, prices, future_candles_list, stop_loss, take_profit, show_output=True
):
    development_end = int(len(X) * 0.8)
    windows = [
        (int(development_end * 0.60), int(development_end * 0.70)),
        (int(development_end * 0.70), int(development_end * 0.80)),
        (int(development_end * 0.80), int(development_end * 0.90)),
        (int(development_end * 0.90), development_end)
    ]
    results = []

    if show_output:
        print("\nWALK FORWARD TEST — DEVELOPMENT DATA")
        print("window | train_end | test_end | conf | trades | winrate | model_return | drawdown | PF | status")

    for number, (train_end, test_end) in enumerate(windows, start=1):
        result = run_test_window(
            X, y, prices, future_candles_list, train_end, test_end,
            CONFIDENCE_LEVELS, stop_loss, take_profit, LEVERAGE, COMMISSION
        )
        results.append(result)

        if show_output:
            print(
                f"{number} | {train_end} | {test_end} | "
                f"{result['confidence']} | {result['trades']} | "
                f"{result['winrate']:.2f} | {result['total_return']:.2f} | "
                f"{result['max_drawdown']:.2f} | {result['profit_factor']:.2f} | "
                f"{result['status']}"
            )
            if result["status"] == "SKIPPED":
                print("reason:", result["reason"])

    evaluated = [result for result in results if result["status"] != "SKIPPED"]
    all_returns = [value for result in results for value in result["returns"]]
    combined = summarize_returns(all_returns)
    passed_windows = sum(result["status"] == "PASSED" for result in results)
    passed = (
        len(evaluated) == len(windows)
        and passed_windows >= 3
        and combined["trades"] >= 30
        and combined["total_return"] > 0
        and combined["profit_factor"] >= 1.1
        and combined["max_drawdown"] <= 20
    )
    status = "PASSED" if passed else "FAILED"
    count = len(evaluated)

    if show_output:
        print("\nWALK FORWARD SUMMARY")
        print("development_end:", development_end)
        print("evaluated_windows:", count)
        print("passed_windows:", passed_windows)
        print("total_windows:", len(windows))
        print("total_trades:", combined["trades"])
        print("model_return:", round(combined["total_return"], 2))
        print("max_drawdown:", round(combined["max_drawdown"], 2))
        print("profit_factor:", round(combined["profit_factor"], 2))
        print("WALK STATUS:", status)

    return {
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "leverage": LEVERAGE,
        "passed_windows": passed_windows,
        "total_windows": len(windows),
        "total_trades": combined["trades"],
        "total_return": combined["total_return"],
        "avg_drawdown": sum(result["max_drawdown"] for result in evaluated) / count if count else 0.0,
        "avg_profit_factor": sum(result["profit_factor"] for result in evaluated) / count if count else 0.0,
        "status": status
    }

def run_tp_sl_grid(X, y, prices, future_candles_list):
    print()
    print("TP/SL GRID TEST")
    print("sl | tp | leverage | passed | trades | total_return | avg_drawdown | avg_profit_factor | status")

    grid_results = []

    for stop_loss in STOP_LOSS_GRID:
        for take_profit in TAKE_PROFIT_GRID:
            result = run_walk_forward(
                X=X,
                y=y,
                prices=prices,
                future_candles_list=future_candles_list,
                stop_loss=stop_loss,
                take_profit=take_profit,
                show_output=False
            )

            grid_results.append(result)

            print(
                result["stop_loss"],
                "|",
                result["take_profit"],
                "|",
                result["leverage"],
                "|",
                f'{result["passed_windows"]}/{result["total_windows"]}',
                "|",
                result["total_trades"],
                "|",
                round(result["total_return"], 2),
                "|",
                round(result["avg_drawdown"], 2),
                "|",
                round(result["avg_profit_factor"], 2),
                "|",
                result["status"]
            )

    valid_results = [
        result for result in grid_results
        if result["status"] == "PASSED"
        and result["passed_windows"] >= 3
        and result["total_trades"] >= 30
        and result["total_return"] > 0
        and result["avg_profit_factor"] >= 1.1
        and result["avg_drawdown"] <= 20
    ]

    print()
    print("TP/SL GRID SUMMARY")

    if not valid_results:
        print("GRID STATUS: FAILED")
        print("No stable TP/SL combination found.")
        return None

    best_result = max(
        valid_results,
        key=lambda result: (
            result["passed_windows"],
            result["avg_profit_factor"],
            result["total_return"],
            -result["avg_drawdown"]
        )
    )

    print("GRID STATUS: PASSED")
    print("best_stop_loss:", best_result["stop_loss"])
    print("best_take_profit:", best_result["take_profit"])
    print("leverage:", best_result["leverage"])
    print("passed_windows:", best_result["passed_windows"])
    print("total_windows:", best_result["total_windows"])
    print("total_trades:", best_result["total_trades"])
    print("total_return:", round(best_result["total_return"], 2))
    print("avg_drawdown:", round(best_result["avg_drawdown"], 2))
    print("avg_profit_factor:", round(best_result["avg_profit_factor"], 2))

    return best_result
def build_backtest_dataset(candles, momentum_transform=None):
    if momentum_transform is None:
        momentum_transform = normalize_momentum

    closes = [candle["close"] for candle in candles]
    ema_values = ema(closes)
    rsi_values = rsi(closes)
    momentum_values = momentum(closes)
    avg_volumes = average_volume(candles)
    volatility_values = volatility(candles)
    regimes = market_regime(closes)

    X = []
    y = []
    prices = []
    future_candles_list = []

    for i in range(20, len(closes) - PREDICTION_HORIZON):
        price = closes[i]
        future_price = closes[i + PREDICTION_HORIZON]

        features = [
            1 if regimes[i - 20] == "TREND" else 0,
            1 if price > ema_values[i - 13] else 0,
            normalize_rsi(rsi_values[i - 14]),
            momentum_transform(momentum_values[i - 5], price),
            normalize_volume(candles[i]["volume"], avg_volumes[i - 10]),
            normalize_volatility(
                candles[i]["high"] - candles[i]["low"],
                volatility_values[i - 10]
            )
        ]

        target_return = (future_price - price) / price
        if target_return > 0.002:
            label = 1
        elif target_return < -0.002:
            label = 0
        else:
            label = None

        X.append(features)
        y.append(label)
        prices.append(price)
        future_candles_list.append(
            candles[i + 1:i + 1 + PREDICTION_HORIZON]
        )

    return X, y, prices, future_candles_list


def compare_momentum(symbol="BTCUSDT"):
    candles = get_klines(symbol=symbol, limit=1000, category="linear")

    baseline = build_backtest_dataset(candles)
    signed = build_backtest_dataset(
        candles,
        momentum_transform=lambda value, price: value / price
    )

    if baseline[1:] != signed[1:]:
        raise RuntimeError("Experiment labels, prices or future candles differ")

    print("EXPERIMENT ROWS:", len(baseline[0]))
    print("DEVELOPMENT ROWS:", int(len(baseline[0]) * 0.8))

    results = {}
    for name, dataset in (("ABS", baseline), ("SIGNED", signed)):
        print("\nMOMENTUM EXPERIMENT:", name)
        results[name] = run_walk_forward(
            *dataset,
            stop_loss=STOP_LOSS,
            take_profit=TAKE_PROFIT
        )

    return results
def run_backtest(symbol="BTCUSDT", *, candles=None):
    """Run the backtest with validated H1 candles when supplied."""
    print("Running backtest for:", symbol)

    if candles is None:
        candles = get_klines(symbol=symbol, limit=1000, category="linear")

    if not candles:
        raise ValueError("Backtest requires closed candles")

    print("CANDLES LOADED:", len(candles))
    
    X, y, prices, future_candles_list = build_backtest_dataset(candles)

    print("DATASET SIZE FOR WALK:", len(X))
    print("LABELLED ROWS:", sum(label is not None for label in y))
    print("NEUTRAL ROWS:", sum(label is None for label in y))
    walk_result = run_walk_forward(
        X, y, prices, future_candles_list, STOP_LOSS, TAKE_PROFIT
    )
    split_index = int(len(X) * 0.8)
    holdout = run_test_window(
        X, y, prices, future_candles_list, split_index, len(X),
        CONFIDENCE_LEVELS, STOP_LOSS, TAKE_PROFIT, LEVERAGE, COMMISSION
    )

    print("\nCONFIDENCE VALIDATION")
    print("conf | trades | avg_return | model_return | drawdown | PF")
    for candidate in holdout["validation_results"]:
        print(
            f"{candidate['confidence']} | {candidate['trades']} | "
            f"{candidate['avg_return']:.4f} | {candidate['total_return']:.2f} | "
            f"{candidate['max_drawdown']:.2f} | {candidate['profit_factor']:.2f}"
        )

    holdout_passed = (
        holdout["status"] != "SKIPPED"
        and holdout["trades"] >= 50
        and holdout["profit_factor"] >= 1.2
        and holdout["total_return"] > 0
        and holdout["max_drawdown"] <= 15
    )
    holdout_status = (
        "SKIPPED" if holdout["status"] == "SKIPPED"
        else "PASSED" if holdout_passed else "FAILED"
    )
    final_status = (
        "PASSED" if walk_result["status"] == "PASSED" and holdout_passed
        else "FAILED"
    )

    print("\nHOLDOUT TEST")
    print("selected confidence:", holdout["confidence"])
    print("trades:", holdout["trades"])
    print("winrate:", round(holdout["winrate"], 2))
    print("avg_return:", round(holdout["avg_return"], 4))
    print("model_return:", round(holdout["total_return"], 2))
    print("max_drawdown:", round(holdout["max_drawdown"], 2))
    print("profit_factor:", round(holdout["profit_factor"], 2))
    print("HOLDOUT STATUS:", holdout_status)
    if holdout["status"] == "SKIPPED":
        print("reason:", holdout["reason"])
    print("FINAL STATUS:", final_status)

    keys = (
        "confidence", "trades", "wins", "losses", "winrate",
        "avg_return", "total_return", "max_drawdown", "profit_factor"
    )
    result = {key: holdout[key] for key in keys}
    result["executions"] = holdout["executions"]
    result.update({
        "symbol": symbol,
        "status": final_status,
        "walk_status": walk_result["status"],
        "final_status": final_status
    })
    return result