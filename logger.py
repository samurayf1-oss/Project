import csv
import os

def log_signal(price, ema, rsi, signal):
    file_exists = os.path.isfile("signals_log.csv")

    with open("signals_log.csv","a", newline="") as file:
        writer = csv.writer(file)

        if not file_exists:
            writer.writerow(["price", "ema", "rsi", "signal"])
        writer.writerow([price, ema, rsi, signal])