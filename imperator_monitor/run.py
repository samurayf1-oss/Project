"""Read-only live monitor for Imperator's paper_trades.csv. Python 3.9+."""

import argparse
import ast
from collections import Counter, OrderedDict
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from math import isfinite
import os
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
import webbrowser


HOUR_MS = 3_600_000
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,24}USDT$")
ASSET_DIR = Path(__file__).resolve().parent
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.mjs": ("app.mjs", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/vendor/lightweight-charts.standalone.production.js": (
        "vendor/lightweight-charts.standalone.production.js", "text/javascript; charset=utf-8"
    ),
}
CONFIG_KEYS = {
    "STOP_LOSS", "TAKE_PROFIT", "LEGACY_POSITION_SIZE_USDT", "LEGACY_LEVERAGE",
    "LEGACY_FEE_RATE", "MAX_SIGNAL_AGE_MINUTES"
}


@contextmanager
def shared_text(path):
    """Allow the Windows bot to atomically replace its CSV while we read it."""
    if os.name != "nt":
        with Path(path).open("r", encoding="utf-8-sig", newline="") as file:
            yield file
        return
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(Path(path).resolve()), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    with os.fdopen(descriptor, "r", encoding="utf-8-sig", newline="") as file:
        yield file


def finite_number(value, positive=False):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not isfinite(value) or (positive and value <= 0):
        return None
    return value


def valid_symbol(value):
    value = str(value or "").strip().upper()
    return value if SYMBOL_RE.fullmatch(value) else None


def parse_time(value, naive_timezone=None):
    """Naive bot timestamps use this computer's local timezone by default."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None and naive_timezone is not None:
            parsed = parsed.replace(tzinfo=naive_timezone)
        stamp = parsed.timestamp()
        return stamp if isfinite(stamp) and stamp > 0 else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def read_literal_settings(project):
    values, warnings = {}, []
    for name in ("settings.py", "paper_trading.py"):
        path = project / name
        if not path.exists():
            warnings.append("Не найден " + name + ": часть настроек неизвестна")
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in tree.body:
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in CONFIG_KEYS:
                        try:
                            values[target.id] = ast.literal_eval(node.value)
                        except (ValueError, TypeError, SyntaxError):
                            values.pop(target.id, None)
                            warnings.append("Не удалось прочитать настройку " + target.id)
        except (OSError, UnicodeError, SyntaxError) as error:
            warnings.append("Не удалось прочитать настройки из " + name + ": " + str(error))
    return values, warnings


class Journal:
    def __init__(self, project, naive_timezone=None):
        self.project = Path(project).resolve()
        self.naive_timezone = naive_timezone

    def snapshot(self):
        now = time.time()
        settings, warnings = read_literal_settings(self.project)
        stop = finite_number(settings.get("STOP_LOSS"), positive=True)
        take = finite_number(settings.get("TAKE_PROFIT"), positive=True)
        stop = stop if stop is not None and stop < 1 else None
        take = take if take is not None and take < 1 else None
        path = self.project / "paper_trades.csv"
        trades, symbols = [], {"BTCUSDT", "ETHUSDT"}
        journal_exists = path.exists()
        modified = None
        if journal_exists:
            with shared_text(path) as file:
                # Read one open file descriptor: an atomic bot replacement cannot mix versions.
                text = file.read()
                try:
                    modified = os.fstat(file.fileno()).st_mtime
                except OSError:
                    pass
            reader = csv.DictReader(io.StringIO(text, newline=""))
            fields = set(reader.fieldnames or [])
            required = {"opened_at", "symbol", "side", "entry_price", "status"}
            if not required.issubset(fields):
                raise ValueError("Журнал пуст или у него неверный заголовок; ожидается paper_trades.csv")
            legacy_positions = not {"position_size_usdt", "leverage"}.intersection(fields)
            legacy_fees = "fee_rate" not in fields
            occurrences = Counter()
            for ordinal, row in enumerate(reader, 1):
                if not any(str(value or "").strip() for value in row.values()):
                    continue
                problems = []
                if None in row or any(row.get(key) is None for key in required):
                    problems.append("Неполная CSV-строка")
                symbol = valid_symbol(row.get("symbol"))
                side = str(row.get("side") or "").strip().upper()
                status = str(row.get("status") or "").strip().upper()
                opened = parse_time(row.get("opened_at"), self.naive_timezone)
                closed = parse_time(row.get("closed_at"), self.naive_timezone)
                entry = finite_number(row.get("entry_price"), positive=True)
                exit_price = finite_number(row.get("exit_price"), positive=True)
                if symbol is None:
                    problems.append("Некорректный инструмент")
                else:
                    symbols.add(symbol)
                if side not in {"BUY", "SELL"}:
                    problems.append("Некорректное направление")
                if status not in {"OPEN", "CLOSED"}:
                    problems.append("Неизвестный статус")
                if opened is None or entry is None:
                    problems.append("Неверное время или цена входа")
                if status == "CLOSED" and (closed is None or exit_price is None):
                    problems.append("Не хватает данных выхода")
                if opened is not None and opened > now + 300:
                    problems.append("Вход находится в будущем: проверь пояс времени журнала")
                margin = finite_number(row.get("position_size_usdt"), positive=True)
                leverage = finite_number(row.get("leverage"), positive=True)
                fee = finite_number(row.get("fee_rate"))
                used_legacy = False
                if status == "OPEN" and legacy_positions:
                    margin = finite_number(settings.get("LEGACY_POSITION_SIZE_USDT"), positive=True)
                    leverage = finite_number(settings.get("LEGACY_LEVERAGE"), positive=True)
                    used_legacy = True
                if status == "OPEN" and legacy_fees:
                    fee = finite_number(settings.get("LEGACY_FEE_RATE"))
                    used_legacy = True
                fee = fee if fee is not None and 0 <= fee < 1 else None
                if status == "OPEN" and None in (margin, leverage, fee):
                    problems.append("Не хватает параметров для net PnL")
                stop_price = take_price = None
                if status == "OPEN" and entry is not None and side in {"BUY", "SELL"}:
                    direction = 1 if side == "BUY" else -1
                    stop_price = entry * (1 - direction * stop) if stop is not None else None
                    take_price = entry * (1 + direction * take) if take is not None else None
                # The ID does not change when the same trade is closed.
                key = "\0".join(str(row.get(k) or "") for k in ("opened_at", "symbol", "side", "entry_price"))
                import hashlib
                digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
                occurrence = occurrences[digest]
                occurrences[digest] += 1
                trades.append({
                    "id": digest + "-" + str(occurrence), "ordinal": ordinal,
                    "symbol": symbol or str(row.get("symbol") or "?")[:40],
                    "side": side, "status": status, "entry_price": entry, "exit_price": exit_price,
                    "opened_at": opened, "closed_at": closed,
                    "opened_raw": str(row.get("opened_at") or ""), "closed_raw": str(row.get("closed_at") or ""),
                    "margin": margin, "leverage": leverage, "fee_rate": fee,
                    "confidence": finite_number(row.get("confidence")),
                    "pnl_percent": finite_number(row.get("pnl_percent")),
                    "pnl_usdt": finite_number(row.get("pnl_usdt")),
                    "gross_pnl_percent": finite_number(row.get("gross_pnl_percent")),
                    "fees_usdt": finite_number(row.get("fees_usdt")),
                    "close_reason": str(row.get("close_reason") or "")[:300],
                    "stop_price": stop_price, "take_price": take_price,
                    "used_legacy_defaults": used_legacy,
                    "recorded_fee_known": fee is not None,
                    "problems": problems,
                })
        else:
            warnings.append("paper_trades.csv ещё не создан: ожидаем сохранения сделок ботом")

        signals = {}
        signal_path = self.project / "live_signals.csv"
        if signal_path.exists():
            with shared_text(signal_path) as file:
                for row in csv.reader(file):
                    if len(row) < 2:
                        continue
                    symbol = valid_symbol(row[1])
                    if symbol is None:
                        continue
                    symbols.add(symbol)
                    if len(row) < 5:
                        signals[symbol] = {"valid": False, "fresh": False, "reason": "Неполная строка сигнала"}
                        continue
                    stamp = parse_time(row[0], self.naive_timezone)
                    confidence = finite_number(row[3])
                    valid = stamp is not None and row[2] in {"BUY", "SELL", "HOLD"} and confidence is not None and 0 <= confidence <= 1
                    age = now - stamp if stamp is not None else None
                    max_age = finite_number(settings.get("MAX_SIGNAL_AGE_MINUTES"), positive=True)
                    signals[symbol] = {
                        "valid": valid, "signal": row[2], "confidence": confidence,
                        "timestamp": stamp, "age_seconds": age,
                        "fresh": bool(valid and max_age is not None and 0 <= age <= max_age * 60),
                        "reason": row[4][:500],
                    }
        bad = sum(bool(trade["problems"]) for trade in trades)
        if bad:
            warnings.append("Строки с неполными данными: " + str(bad) + "; неизвестные значения не считаются нулём")
        return {
            "ok": True, "read_at": now, "trades": trades, "symbols": sorted(symbols),
            "signals": signals, "warnings": warnings,
            "journal": {"exists": journal_exists, "modified_at": modified, "rows": len(trades), "problem_rows": bad},
            "timezone": str(self.naive_timezone) if self.naive_timezone is not None else "Локальное время компьютера бота",
            "settings": {"stop_loss": stop, "take_profit": take},
        }


class MarketClient:
    def __init__(self, fetcher=None):
        self.fetcher = fetcher or self.fetch_payload
        self.lock = threading.Lock()
        self.cache = OrderedDict()

    @staticmethod
    def fetch_payload(symbol, end_ms):
        query = {"category": "linear", "symbol": symbol, "interval": "60", "limit": 1000}
        if end_ms is not None:
            query["end"] = end_ms
        url = "https://api.bybit.com/v5/market/kline?" + urlencode(query)
        with urlopen(Request(url, headers={"User-Agent": "ImperatorReadOnlyMonitor/1.0"}), timeout=4) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def normalize(payload, symbol, end_ms):
        if not isinstance(payload, dict):
            raise ValueError("Неверный ответ Bybit")
        if payload.get("retCode") != 0:
            raise ValueError("Bybit: " + str(payload.get("retMsg", "неверный ответ")))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("В ответе Bybit отсутствуют рыночные данные")
        if result.get("symbol") != symbol or result.get("category") != "linear":
            raise ValueError("Bybit вернул другой инструмент или категорию")
        server_ms = int(payload["time"])
        if not -5000 <= int(time.time() * 1000) - server_ms <= 30_000:
            raise ValueError("Ответ Bybit устарел или часы компьютера неточны")
        rows = result.get("list")
        if not isinstance(rows, list) or not rows:
            raise ValueError("Bybit не вернул свечи")
        candles, seen = [], set()
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 7:
                raise ValueError("Неверная строка свечи")
            stamp = int(row[0])
            values = [float(v) for v in row[1:7]]
            o, h, l, c, volume, turnover = values
            if not all(isfinite(v) for v in values) or min(o, h, l, c) <= 0 or min(volume, turnover) < 0:
                raise ValueError("Некорректные числовые данные свечи")
            if not l <= min(o, c) <= max(o, c) <= h:
                raise ValueError("Неверные границы OHLC")
            if stamp <= 0 or stamp > server_ms or stamp in seen or (end_ms is not None and stamp > end_ms):
                raise ValueError("Некорректная дата или дубликат свечи")
            seen.add(stamp)
            candles.append({"time": stamp // 1000, "open": o, "high": h, "low": l, "close": c, "volume": volume})
        candles.sort(key=lambda c: c["time"])
        if any(b["time"] - a["time"] != 3600 for a, b in zip(candles, candles[1:])):
            raise ValueError("В котировках обнаружен пропуск часовой свечи")
        if end_ms is None and server_ms >= candles[-1]["time"] * 1000 + 2 * HOUR_MS + 2000:
            raise ValueError("Последняя свеча Bybit устарела")
        return {"ok": True, "symbol": symbol, "candles": candles, "price": candles[-1]["close"],
                "server_time": server_ms / 1000, "fetched_at": time.time(), "is_live": end_ms is None,
                "forming": candles[-1]["time"] * 1000 + HOUR_MS > server_ms}

    def get(self, symbol, end_ms=None):
        if valid_symbol(symbol) != symbol:
            raise ValueError("Неверный USDT-инструмент")
        key = (symbol, end_ms)
        # One request at a time prevents multiple browser tabs from duplicating market queries.
        with self.lock:
            cached = self.cache.get(key)
            ttl = 4 if end_ms is None else 900
            if cached and not cached[1]["ok"]:
                ttl = 4
            if cached and time.monotonic() - cached[0] < ttl:
                self.cache.move_to_end(key)
                return cached[1]
            try:
                data = self.normalize(self.fetcher(symbol, end_ms), symbol, end_ms)
            except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
                data = {"ok": False, "symbol": symbol, "error": "Котировки недоступны: " + str(error)[:300],
                        "is_live": end_ms is None}
                ttl = 4
            self.cache[key] = (time.monotonic(), data)
            while len(self.cache) > 64:
                self.cache.popitem(last=False)
            return data


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, journal, market=None):
        self.journal = journal
        self.market = market or MarketClient()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if args and str(args[1] if len(args) > 1 else "") not in {"200", "304"}:
            super().log_message(fmt, *args)

    def send_data(self, status, content, content_type="application/json; charset=utf-8"):
        body = content if isinstance(content, bytes) else json.dumps(content, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        port = self.server.server_address[1]
        hosts = {"127.0.0.1:" + str(port), "localhost:" + str(port)}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in hosts or (origin is not None and origin not in {"http://" + h for h in hosts}):
            self.send_data(403, {"ok": False, "error": "Доступ разрешён только с этой локальной панели"})
            return
        parsed = urlparse(self.path)
        if parsed.path in ASSETS:
            name, content_type = ASSETS[parsed.path]
            try:
                self.send_data(200, (ASSET_DIR / name).read_bytes(), content_type)
            except OSError:
                self.send_data(404, {"ok": False, "error": "Не найден файл панели; распакуй архив целиком"})
            return
        try:
            if parsed.path == "/api/state":
                self.send_data(200, self.server.journal.snapshot())
            elif parsed.path == "/api/market":
                query = parse_qs(parsed.query)
                symbol = valid_symbol(query.get("symbol", ["BTCUSDT"])[0])
                if symbol is None:
                    raise ValueError("Неверный инструмент")
                end = query.get("end", [None])[0]
                end_ms = int(end) if end is not None else None
                if end_ms is not None and not 0 < end_ms <= int(time.time() * 1000) + 60_000:
                    raise ValueError("Неверная граница истории")
                data = self.server.market.get(symbol, end_ms)
                self.send_data(200 if data["ok"] else 502, data)
            else:
                self.send_data(404, {"ok": False, "error": "Неизвестный адрес"})
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, csv.Error, OverflowError) as error:
            self.send_data(400, {"ok": False, "error": str(error)[:500]})

    def do_POST(self):
        self.send_data(405, {"ok": False, "error": "Панель поддерживает только чтение"})


def offset_timezone(value):
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", value)
    if not match:
        raise argparse.ArgumentTypeError("Ожидается смещение вида +07:00 или +00:00")
    hours, minutes = int(match[2]), int(match[3])
    if hours > 14 or minutes > 59 or (hours == 14 and minutes):
        raise argparse.ArgumentTypeError("Некорректное смещение времени")
    delta = timedelta(hours=hours, minutes=minutes)
    return timezone(delta if match[1] == "+" else -delta)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ASSET_DIR.parent, help="Папка с paper_trades.csv")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-offset", type=offset_timezone, default=None, help="Пояс старых записей, например +07:00; по умолчанию часы этого ПК")
    parser.add_argument("--no-browser", action="store_true")
    options = parser.parse_args(argv)
    if not options.project.is_dir():
        parser.error("Папка проекта не найдена")
    if not 1 <= options.port <= 65535:
        parser.error("Порт должен быть от 1 до 65535")
    server = MonitorServer(("127.0.0.1", options.port), Journal(options.project, options.log_offset))
    url = "http://127.0.0.1:" + str(server.server_address[1])
    print("IMPERATOR LIVE MONITOR:", url, flush=True)
    print("PROJECT:", options.project.resolve(), flush=True)
    print("Journal refresh: 2 seconds. Market refresh: 5 seconds. Ctrl+C to stop the panel.", flush=True)
    if not options.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
