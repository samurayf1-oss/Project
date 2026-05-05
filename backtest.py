from data import get_klines
from indicators import ema, rsi, momentum, average_volume, volatility, market_regime
from features import (
    normalize_rsi,
    normalize_momentum,
    normalize_volume,
    normalize_volatility
)
from model import train_model, predict_signal, show_feature_importance

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

    start_index = 20

    # print("FOR START:", start_index)
    # print("FOR END:", len(ema_values) - 5)

    for i in range(start_index, len(ema_values) - 5):
        price = closes[i]
        future_price = closes[i + 5]

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
        print("BEST RESULT")
        print("confidence:", best_result["confidence"])
        print("trades:", best_result["trades"])
        print("winrate:", round(best_result["winrate"]))
        print("total_return:", round(best_result["total_return"], 2), "%")
        print("max_drawdown:", round(best_result["max_drawdown"], 2), "%")
        print("profit_factor:", round(best_result["profit_factor"], 2))
        print("STATUS: PASSED")
        best_result["symbol"] = symbol
        best_result["status"] = "PASSED"
        return best_result
    else:
        print()
        print("BEST RESULT")
        print("STATUS: FAILED")
        return{
            "symbol": symbol,
            "status": "FAILED"
        }