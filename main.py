from backtest import run_backtest

symbols = [
    "BTCUSDT",
    "ETHUSDT",
]
for symbol in symbols:
    print()
    print("=" * 50)
    print("SYMBOL:", symbol)
    print("=" * 50)

    run_backtest(symbol)