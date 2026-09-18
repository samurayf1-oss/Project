import json
import os
import urllib.parse
import urllib.request
from typing import Dict, List
from dotenv import load_dotenv

load_dotenv()


def _is_enabled() -> bool:
    enabled = os.getenv("TG_ENABLED", "1").strip().lower()
    return enabled in {"1", "true", "yes", "on"}


def _bot_token() -> str:
    return os.getenv("TG_BOT_TOKEN", "").strip()


def _chat_id() -> str:
    return os.getenv("TG_CHAT_ID", "").strip()


def can_send() -> bool:
    return _is_enabled() and _bot_token() != "" and _chat_id() != ""


def send_message(text: str) -> bool:
    if not can_send():
        return False

    token = _bot_token()
    chat_id = _chat_id()
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(url=url, data=body, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw)
            return bool(parsed.get("ok"))
    except Exception:
        return False


def diagnostics() -> str:
    if not _is_enabled():
        return "Telegram disabled: TG_ENABLED is off"
    if _bot_token() == "":
        return "Telegram disabled: TG_BOT_TOKEN is empty"
    if _chat_id() == "":
        return "Telegram disabled: TG_CHAT_ID is empty"
    return "Telegram is configured"


def format_trade_opened(trade: Dict[str, str]) -> str:
    return (
        "OPENED\n"
        f"{trade.get('symbol', '')} {trade.get('side', '')}\n"
        f"entry: {trade.get('entry_price', '')}\n"
        f"conf: {trade.get('confidence', '')}\n"
        f"time: {trade.get('opened_at', '')}"
    )


def format_trade_closed(trade: Dict[str, str]) -> str:
    return (
        "CLOSED\n"
        f"{trade.get('symbol', '')} {trade.get('side', '')}\n"
        f"entry: {trade.get('entry_price', '')} -> exit: {trade.get('exit_price', '')}\n"
        f"pnl: {trade.get('pnl_percent', '')}% | {trade.get('pnl_usdt', '')} USDT\n"
        f"reason: {trade.get('close_reason', '')}\n"
        f"closed: {trade.get('closed_at', '')}"
    )


def format_cycle_summary(summary: Dict[str, float]) -> str:
    return (
        "CYCLE SUMMARY\n"
        f"trades total: {summary['total_trades']}\n"
        f"open: {summary['open_trades']} | closed: {summary['closed_trades']}\n"
        f"wins: {summary['wins']} | losses: {summary['losses']}\n"
        f"winrate: {summary['winrate']:.2f}%\n"
        f"closed pnl: {summary['closed_pnl_usdt']:.4f} USDT"
    )


def _to_float(value: str) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except ValueError:
        return 0.0


def build_trade_summary(trades: List[Dict[str, str]]) -> Dict[str, float]:
    closed = [trade for trade in trades if trade.get("status") == "CLOSED"]
    opened = [trade for trade in trades if trade.get("status") == "OPEN"]

    wins = [trade for trade in closed if _to_float(trade.get("pnl_usdt", "")) > 0]
    losses = [trade for trade in closed if _to_float(trade.get("pnl_usdt", "")) <= 0]
    winrate = (len(wins) / len(closed) * 100) if closed else 0.0
    closed_pnl = sum(_to_float(trade.get("pnl_usdt", "")) for trade in closed)

    return {
        "total_trades": len(trades),
        "open_trades": len(opened),
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "winrate": winrate,
        "closed_pnl_usdt": closed_pnl,
    }
