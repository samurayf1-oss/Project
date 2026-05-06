from data import get_klines
from indicators import ema, rsi, momentum, average_volume, volatility, market_regime
from settings import (
    STOP_LOSS,
    TAKE_PROFIT,
    LEVERAGE,
    COMMISSION,
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

def simulate_trade(side, entry_price, future_candles, stop_loss, take_profit, leverage, commission):
    for candle in future_candles:
        high = candle["high"]
        low = candle["low"]
        close = candle["close"]

        if side == "BUY":
            stop_price = entry_price * (1- stop_loss)
            take_price = entry_price * (1 + take_profit)
            
            if low <= stop_price:
                return (-stop_loss * leverage) - commission
            if high >= take_price:
                return (take_profit * leverage) - commission
            
        if side == "SELL":
            stop_price = entry_price * (1 + stop_loss)
            take_price = entry_price * (1 - take_profit) 

            if high >= stop_price:
                return (-stop_loss * leverage) - commission
            if low <= take_price:
                return (take_profit * leverage) - commission
    
    final_close = future_candles[-1]["close"]
    price_return = (final_close - entry_price) / entry_price
    
    if side =="BUY":
        return (price_return * leverage) - commission
    return (-price_return * leverage) - commission

def run_test_window(
    X,
    y,
    prices,
    future_candles_list,
    train_end,
    test_end,
    confidence_levels,
    stop_loss,
    take_profit,
    leverage,
    commission
):
    X_train = X[:train_end]
    y_train = y[:train_end]

    X_test = X[train_end:test_end]
    y_test = y[train_end:test_end]

    test_prices = prices[train_end:test_end]
    test_future_candles_list = future_candles_list[train_end:test_end]

    if len(X_train) == 0 or len(y_train) == 0 or len(X_test) == 0:
        return None
    
    if len(set(y_train)) < 2:
        return None

    model = train_model(X_train, y_train)

    results = []

    for min_confidence in confidence_levels:
        returns = []
        equity_curve = [1.0]

        for i in range(len(X_test)):
            probability = model.predict_proba([X_test[i]])[0]

            sell_confidence = probability[0]
            buy_confidence = probability[1]

            if buy_confidence > min_confidence:
                prediction = 1
            elif sell_confidence > min_confidence:
                prediction = 0
            else:
                continue

            entry_price = test_prices[i]
            future_candles = test_future_candles_list[i]

            if prediction == 1:
                trade_return = simulate_trade(
                    side="BUY",
                    entry_price=entry_price,
                    future_candles=future_candles,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    leverage=leverage,
                    commission=commission
                )
            else:
                trade_return = simulate_trade(
                    side="SELL",
                    entry_price=entry_price,
                    future_candles=future_candles,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    leverage=leverage,
                    commission=commission
                )

            returns.append(trade_return)
            equity_curve.append(equity_curve[-1] * (1 + trade_return))

        total_trades = len(returns)
        wins = sum(1 for r in returns if r > 0)
        losses = sum(1 for r in returns if r <= 0)

        winrate = wins / total_trades * 100 if total_trades > 0 else 0
        total_return = (equity_curve[-1] - 1) * 100
        avg_return = sum(returns) / len(returns) * 100 if returns else 0

        gross_profit = sum(r for r in returns if r > 0)
        gross_loss = abs(sum(r for r in returns if r < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        peak = equity_curve[0]
        max_drawdown = 0

        for equity in equity_curve:
            if equity > peak:
                peak = equity

            drawdown = (peak - equity) / peak

            if drawdown > max_drawdown:
                max_drawdown = drawdown

        max_drawdown *= 100

        results.append({
            "confidence": min_confidence,
            "trades": total_trades,
            "wins": wins,
            "losses": losses,
            "winrate": winrate,
            "avg_return": avg_return,
            "total_return": total_return,
            "max_drawdown": max_drawdown,
            "profit_factor": profit_factor
        })

    valid_results = [
        result for result in results
        if result["trades"] >= 10
        and result["profit_factor"] >= 1.1
        and result["total_return"] > 0
        and result["max_drawdown"] <= 20
    ]

    if not valid_results:
        return None

    return max(valid_results, key=lambda result: result["profit_factor"])

def run_walk_forward(
    X,
    y,
    prices,
    future_candles_list,
    stop_loss,
    take_profit,
    show_output=True
):
    dataset_size = len(X)

    windows = [
        (int(dataset_size * 0.60), int(dataset_size * 0.70)),
        (int(dataset_size * 0.70), int(dataset_size * 0.80)),
        (int(dataset_size * 0.80), int(dataset_size * 0.90)),
        (int(dataset_size * 0.90), int(dataset_size * 1.00)),
    ]

    if show_output:
        print()
        print("WALK FORWARD TEST")
        print("window | train_end | test_end | conf | trades | winrate | total_return | drawdown | profit_factor | status")

    walk_results = []

    for index, window in enumerate(windows, start=1):
        train_end = window[0]
        test_end = window[1]

        result = run_test_window(
            X=X,
            y=y,
            prices=prices,
            future_candles_list=future_candles_list,
            train_end=train_end,
            test_end=test_end,
            confidence_levels=CONFIDENCE_LEVELS,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=LEVERAGE,
            commission=COMMISSION
        )

        if result is None:
            if show_output:
                print(index, "|", train_end, "|", test_end, "|", "-", "|", 0, "|", 0, "|", 0, "|", 0, "|", 0, "| FAILED")
            continue

        walk_results.append(result)

        if show_output:
            print(
                index,
                "|",
                train_end,
                "|",
                test_end,
                "|",
                result["confidence"],
                "|",
                result["trades"],
                "|",
                round(result["winrate"], 2),
                "|",
                round(result["total_return"], 2),
                "|",
                round(result["max_drawdown"], 2),
                "|",
                round(result["profit_factor"], 2),
                "| PASSED"
            )

    passed_windows = len(walk_results)
    total_windows = len(windows)

    total_trades = sum(result["trades"] for result in walk_results)
    avg_profit_factor = (
        sum(result["profit_factor"] for result in walk_results) / passed_windows
        if passed_windows > 0
        else 0
    )
    avg_drawdown = (
        sum(result["max_drawdown"] for result in walk_results) / passed_windows
        if passed_windows > 0
        else 0
    )
    total_return = sum(result["total_return"] for result in walk_results)

    status = "PASSED" if passed_windows >= 3 else "FAILED"

    if show_output:
        print()
        print("WALK FORWARD SUMMARY")
        print("stop_loss:", stop_loss)
        print("take_profit:", take_profit)
        print("leverage:", LEVERAGE)
        print("passed_windows:", passed_windows)
        print("total_windows:", total_windows)
        print("WALK STATUS:", status)

    return {
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "leverage": LEVERAGE,
        "passed_windows": passed_windows,
        "total_windows": total_windows,
        "total_trades": total_trades,
        "total_return": total_return,
        "avg_drawdown": avg_drawdown,
        "avg_profit_factor": avg_profit_factor,
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
        if result["passed_windows"] >= 3
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

def run_backtest(symbol="BTCUSDT"):
    print("Running backtest for:", symbol)
    
    candles = get_klines(symbol=symbol, limit=1000, category="linear")
    print("CANDLES LOADED:", len(candles))
    
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
    future_prices = []
    future_candles_list = []

    start_index = 20

    # print("FOR START:", start_index)
    # print("FOR END:", len(ema_values) - 5)

    for i in range(start_index, len(ema_values) - 5):
        price = closes[i]
        future_price = closes[i + 5]
        future_candles = candles[i + 1:i + 6]

        current_ema = ema_values[i - 14]
        current_rsi = rsi_values[i - 14]
        current_momentum = momentum_values[i - 5]
        current_volume = candles[i]["volume"]
        current_avg_volume = avg_volumes[i - 10]
        current_volatility = candles[i]["high"] - candles[i]["low"]
        current_avg_volatility = volatility_values[i - 10]
        current_regime = 1 if regimes[i - 20] == "TREND" else 0


        features = [ 
            current_regime,
            1 if price > current_ema else 0, 
            normalize_rsi(current_rsi),  
            normalize_momentum(current_momentum, price),
            normalize_volume(current_volume, current_avg_volume),
            normalize_volatility(current_volatility, current_avg_volatility)
        ]

        target_return = (future_price - price) / price
        min_move = 0.002    # 0.2%
        if target_return > min_move:
            label = 1
        elif target_return < -min_move:
            label = 0
        else:
            continue

        # print("features:", features)
        # print("LABEL:", label)

        X.append(features)
        y.append(label)
        
        prices.append(price)
        future_prices.append(future_price)
        future_candles_list.append(future_candles)
 
        print("DATASET SIZE FOR WALK:", len(X))
    
        confidence_levels = [0.50, 0.55, 0.60, 0.65, 0.70]
        commission = 0.001

        stop_loss = 0.005
        take_profit = 0.01

        dataset_size = len(X)

        windows = [
            (int(dataset_size * 0.60), int(dataset_size * 0.70)),
            (int(dataset_size * 0.70), int(dataset_size * 0.80)),
            (int(dataset_size * 0.80), int(dataset_size * 0.90)),
            (int(dataset_size * 0.90), int(dataset_size * 1.00)),
        ]

        print()
        print("WALK FORWARD TEST")
        print("window | train_end | test_end | conf | trades | winrate | total_return | drawdown | profit_factor | status")

        walk_results = []

        for index, window in enumerate(windows, start=1):
            train_end = window[0]
            test_end = window[1]

            result = run_test_window(
                X=X,
                y=y,
                prices=prices,
                future_candles_list=future_candles_list,
                train_end=train_end,
                test_end=test_end,
                confidence_levels=CONFIDENCE_LEVELS,
                stop_loss=STOP_LOSS,
                take_profit=TAKE_PROFIT,
                leverage=LEVERAGE,
                commission=COMMISSION
            )

            if result is None:
                print(index, "|", train_end, "|", test_end, "|", "-", "|", 0, "|", 0, "|", 0, "|", 0, "|", 0, "| FAILED")
                continue

            walk_results.append(result)

            print(
                index,
                "|",
                train_end,
                "|",
                test_end,
                "|",
                result["confidence"],
                "|",
                result["trades"],
                "|",
                round(result["winrate"], 2),
                "|",
                round(result["total_return"], 2),
                "|",
                round(result["max_drawdown"], 2),
                "|",
               round(result["profit_factor"], 2),
                "| PASSED"
            )

        passed_windows = len(walk_results)
        total_windows = len(windows)

        print()
        print("WALK FORWARD SUMMARY")
        print("stop_loss:", STOP_LOSS)
        print("take_profit:", TAKE_PROFIT)
        print("leverage:", LEVERAGE)
        print("passed_windows:", passed_windows)
        print("total_windows:", total_windows)

        if passed_windows >= 3:
            walk_status = "PASSED"
        else:
            walk_status = "FAILED"
        print("WALK STATUS:", walk_status)
        
        # run_tp_sl_grid(
        #   X=X,
        #   y=y,
        #   prices=prices,
        #   future_candles_list=future_candles_list
        # )

    split_index = int(len(X) * 0.8)

    X_train = X[:split_index]
    y_train = y[:split_index]
    X_test = X[split_index:]
    y_test = y[split_index:]

    test_prices = prices[split_index:]
    test_future_prices = future_prices[split_index:]
    
    # print("CANDLES COUNT IN BACKTEST:",len(candles))
    # print("EMA VALUES:", len(ema_values))
    # print("RSI VALUES:", len(rsi_values))
    # print("MOMENTUM VALUES:", len(momentum_values))
    # print("AVG VOLUMES:",len(avg_volumes))
    # print("VOLATILITY VALUES:", len(volatility_values))
    # print("REGIMES:", len(regimes))
    
    if len(X_train) == 0 or len(y_train) == 0:
        print("ERROR: training dataset is empty")
        return

    model = train_model(X_train, y_train)
    show_feature_importance(model)
    
    wins = 0
    losses = 0
    returns = []
    equity_curve = [1.0]

    commission = 0.001 # 0.1% потом нужно уточнить
    
    confidence_levels = [0.50, 0.55, 0.60, 0.65, 0.70]

    print()
    print("CONFIDENCE TEST")
    print("conf | trades | wins | losses | winrate | avg_return | total_return | drawdown | profit_factor")

    results = []

    for min_confidence in confidence_levels:
        returns = []
        equity_curve = [1.0]

        for i in range(len(X_test)):
            
            probability = model.predict_proba([X_test[i]])[0]
            
            sell_confidence = probability[0]
            buy_confidence = probability[1]

            if buy_confidence > min_confidence:
                prediction = 1
            elif sell_confidence > min_confidence:
                prediction = 0
            else:
                continue

            entry_price = test_prices[i]
            exit_price = test_future_prices[i]

            price_return = (exit_price - entry_price) / entry_price

            if prediction == 1:
                trade_return = price_return - commission
            else:
                trade_return = -price_return - commission

            returns.append(trade_return)
            equity_curve.append(equity_curve[-1] * (1 + trade_return))
    
        total_trades = len(returns)
        wins = sum(1 for r in returns if r > 0)
        losses = sum(1 for r in returns if r <= 0)

            
        winrate = wins / total_trades * 100 if total_trades > 0 else 0

        total_return = (equity_curve[-1] - 1) * 100
        avg_return = sum(returns) / len(returns) * 100 if returns else 0

        gross_profit = sum(r for r in returns if r > 0)
        gross_loss = abs(sum(r for r in returns if r < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        peak = equity_curve[0]
        max_drawdown = 0

        for equity in equity_curve:
            if equity > peak:
                peak = equity

            drawdown = (peak - equity) / peak

            if drawdown > max_drawdown:
                max_drawdown = drawdown
            
        max_drawdown *= 100
        
        results.append({
            "confidence": min_confidence,
            "trades": total_trades,
            "wins": wins,
            "losses": losses,
            "winrate": winrate,
            "avg_return": avg_return,
            "total_return": total_return,
            "max_drawdown": max_drawdown,
            "profit_factor": profit_factor
        })

        print(
            min_confidence,
            "|",
            total_trades,
            "|",
            wins,
            "|",
            losses,
            "|",
            round(winrate, 2),
            "|",
            round(avg_return, 4),
            "|",
            round(total_return, 2),
            "|",
            round(max_drawdown, 2),
            "|",
            round(profit_factor, 2)
        )

    valid_results = [
        result for result in results
        if result["trades"] >= 50
        and result["profit_factor"] >= 1.2
        and result["total_return"] > 0
        and result["max_drawdown"] <= 15
    ]

    if valid_results:
        best_result = max(valid_results, key=lambda result: result["profit_factor"])
        print()
        print("CONFIDENCE TEST RESULT")
        print("confidence:", best_result["confidence"])
        print("trades:", best_result["trades"])
        print("winrate:", round(best_result["winrate"]))
        print("total_return:", round(best_result["total_return"], 2), "%")
        print("max_drawdown:", round(best_result["max_drawdown"], 2), "%")
        print("profit_factor:", round(best_result["profit_factor"], 2))
        print("STATUS: PASSED")
        best_result["symbol"] = symbol
        best_result["status"] = "PASSED"
        best_result["walk_status"] = walk_status

        if walk_status == "PASSED" and best_result["status"] == "PASSED":
            best_result["final_status"] = "PASSED"
        else:
             best_result["final_status"] = "FAILED"

        best_result["status"] = best_result["final_status"]
        return best_result
    else:
        print()
        print("CONFIDENCE TEST RESULT")
        print("STATUS: FAILED")
        return{
            "symbol": symbol,
            "status": "FAILED",
            "walk_status": walk_status,
            "final_status": "FAILED"
        }