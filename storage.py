import json
import re
import sqlite3
import time
from typing import Any

from bot_config import DB_PATH, CHAT_SETTINGS_PATH, STATS_PATH, chat_settings, quote_stats


def init_storage_db() -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS quote_stats (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS chat_settings (chat_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, message_text TEXT, timestamp REAL)"
        )
        connection.commit()


def save_message_to_history(chat_id: int, text: str) -> None:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return
    init_storage_db()
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            "INSERT INTO chat_history (chat_id, message_text, timestamp) VALUES (?, ?, ?)",
            (str(chat_id), cleaned, time.time()),
        )
        connection.commit()


def get_recent_chat_context(chat_id: int, limit: int = 15) -> str:
    init_storage_db()
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            "SELECT message_text FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (str(chat_id), max(1, int(limit))),
        ).fetchall()
    if not rows:
        return ""
    messages = [row[0] for row in rows if row and row[0]]
    messages.reverse()
    formatted = "\n".join(f"- {item}" for item in messages)
    return f"Контекст последних сообщений в чате:\n{formatted}"


def load_quote_stats() -> dict[str, dict[str, Any]]:
    global quote_stats
    init_storage_db()

    try:
        with sqlite3.connect(DB_PATH) as connection:
            rows = connection.execute("SELECT key, payload FROM quote_stats").fetchall()
            if rows:
                quote_stats = {key: json.loads(payload) for key, payload in rows if isinstance(payload, str)}
                return quote_stats
    except Exception:
        quote_stats = {}

    if STATS_PATH.exists():
        try:
            with STATS_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                quote_stats = data
                save_quote_stats()
                return quote_stats
        except Exception:
            quote_stats = {}
    else:
        quote_stats = {}
    return quote_stats


def save_quote_stats() -> None:
    init_storage_db()
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("DELETE FROM quote_stats")
        for key, entry in quote_stats.items():
            connection.execute(
                "INSERT INTO quote_stats (key, payload) VALUES (?, ?)",
                (key, json.dumps(entry, ensure_ascii=False)),
            )
        connection.commit()


def load_chat_settings() -> dict[str, dict[str, Any]]:
    global chat_settings
    init_storage_db()

    try:
        with sqlite3.connect(DB_PATH) as connection:
            rows = connection.execute("SELECT chat_id, payload FROM chat_settings").fetchall()
            if rows:
                chat_settings = {chat_id: json.loads(payload) for chat_id, payload in rows if isinstance(payload, str)}
                return chat_settings
    except Exception:
        chat_settings = {}

    if CHAT_SETTINGS_PATH.exists():
        try:
            with CHAT_SETTINGS_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                chat_settings = data
                save_chat_settings()
                return chat_settings
        except Exception:
            chat_settings = {}
    else:
        chat_settings = {}
    return chat_settings


def save_chat_settings() -> None:
    init_storage_db()
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("DELETE FROM chat_settings")
        for chat_id, entry in chat_settings.items():
            connection.execute(
                "INSERT INTO chat_settings (chat_id, payload) VALUES (?, ?)",
                (chat_id, json.dumps(entry, ensure_ascii=False)),
            )
        connection.commit()
