import asyncio
import base64
import hashlib
import html
import io
import json
import os
import random
import re
import sqlite3
import time
import traceback
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from openai import AsyncOpenAI
import httpx

# Load env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_CHAT = os.getenv("MODEL_CHAT", "meta-llama/llama-3.1-8b-instruct")
MODEL_VISION = os.getenv("MODEL_VISION", "openai/gpt-4o-mini")
MODEL_WHISPER = os.getenv("MODEL_WHISPER", "openai/whisper-large-v3")
BOT_USERNAME: str | None = None


def resolve_data_dir() -> Path:
    configured = os.getenv("DATA_DIR") or os.getenv("PERSISTENT_DIR") or os.getenv("STORAGE_DIR")
    if configured:
        path = Path(configured).expanduser()
    elif os.name != "nt":
        path = Path("/data")
    else:
        path = Path(__file__).resolve().parent / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_DIR = resolve_data_dir()
STATS_PATH = DATA_DIR / "quote_stats.json"
CHAT_SETTINGS_PATH = DATA_DIR / "chat_settings.json"
DB_PATH = DATA_DIR / "bot_data.sqlite3"
ADMIN_IDS = {int(item) for item in os.getenv("ADMIN_IDS", "").split(",") if item.strip().isdigit()}
AUTO_QUOTE_ENABLED = True
AI_RESPONSE_ENABLED = True
chat_settings: dict[str, dict[str, Any]] = {}
pending_suggestions: dict[int, int] = {}
pending_admin_comments: dict[int, str] = {}
suggestion_anonymity: dict[int, bool] = {}
pending_questions: dict[int, int] = {}
question_reply_targets: dict[tuple[int, int], dict[str, Any]] = {}

WELCOME_TEXT = (
    "Привет! Я — бот-агент. Упомяни меня в сообщении и задай вопрос,\n"
    "например: @agentplay_bot Как мне сделать X?\n\n"
    "Если хочешь внести идею или предложку — нажми кнопку ниже.\n"
    "А ещё у нас есть крутое приложение с видео и обновлениями"
)

HELP_TEXT = (
    "Как пользоваться ботом:\n\n"
    "• Упомяни меня в чате и задай вопрос — я отвечу как агент.\n"
    "• Ответь на сообщение командой /q — я сделаю из него цитату.\n"
    "• Используй /top, чтобы посмотреть самые оценённые цитаты.\n"
    "• Нажми кнопку ниже, если хочешь отправить предложку."
)

AGENT_UPDATES_TEXT = (
    "🆕 Что уже добавили в агента:\n\n"
    "• ИИ-ответы по упоминанию бота\n"
    "• Кнопка «Есть вопрос!🤓» для быстрых вопросов\n"
    "• Шаблоны ответов в личных сообщениях\n"
    "• Улучшенные AI-цитаты и автоответы\n"
    "• Кнопка обновлений для быстрого просмотра новинок\n\n"
    "Скоро будет ещё больше фишек — следи за обновлениями!"
)

# Инициализация бота и клиента ИИ
bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()


class AntiFloodMiddleware(BaseMiddleware):
    def __init__(self, limit_seconds: float = 3.0) -> None:
        super().__init__()
        self.limit_seconds = limit_seconds
        self.last_user_action: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        if not hasattr(event, "from_user") or not getattr(event, "from_user", None):
            return await handler(event, data)

        user = event.from_user
        if getattr(user, "is_bot", False):
            return await handler(event, data)

        user_id = getattr(user, "id", None)
        if user_id is None:
            return await handler(event, data)

        text = (getattr(event, "text", None) or getattr(event, "caption", "") or "").strip()
        mention_bot = False
        if BOT_USERNAME:
            mention_bot = f"@{BOT_USERNAME.lower()}" in text.lower()
        else:
            mention_bot = "@" in text and any(part.startswith("@") for part in text.split())
        is_targeted = text.startswith("/q") or mention_bot
        if not is_targeted:
            return await handler(event, data)

        now = time.monotonic()
        last_time = self.last_user_action.get(user_id)
        if last_time is not None and now - last_time < self.limit_seconds:
            try:
                await event.answer("Слишком часто, подожди немного перед следующим запросом.⏳")
            except Exception:
                pass
            return

        self.last_user_action[user_id] = now
        return await handler(event, data)


dp.message.outer_middleware(AntiFloodMiddleware())

# Подключаем AsyncOpenAI к серверу OpenRouter
ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENAI_API_KEY,
)

quote_stats: dict[str, dict[str, Any]] = {}
suggestion_requests: dict[str, dict[str, Any]] = {}


def is_admin_user(user: Any | None) -> bool:
    if not user:
        return False
    if not ADMIN_IDS:
        return True
    return int(getattr(user, "id", 0)) in ADMIN_IDS


def get_chat_setting(chat_id: int | None, key: str, default: Any = True) -> Any:
    if chat_id is None:
        return default
    settings = chat_settings.get(str(chat_id)) or {}
    return settings.get(key, default)


def set_chat_setting(chat_id: int | None, key: str, value: Any) -> None:
    if chat_id is None:
        return
    key_str = str(chat_id)
    entry = chat_settings.get(key_str)
    if not isinstance(entry, dict):
        entry = {}
        chat_settings[key_str] = entry
    entry[key] = value
    save_chat_settings()


def build_admin_markup(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🧠 AI: {'вкл' if get_chat_setting(chat_id, 'ai_enabled', True) else 'выкл'}",
                    callback_data=f"admin_toggle:ai:{chat_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"💬 Автоцитаты: {'вкл' if get_chat_setting(chat_id, 'auto_quote_enabled', True) else 'выкл'}",
                    callback_data=f"admin_toggle:auto_quote:{chat_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📣 Приветствие: {'вкл' if get_chat_setting(chat_id, 'welcome_enabled', True) else 'выкл'}",
                    callback_data=f"admin_toggle:welcome:{chat_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"💡 Предложка: {'вкл' if get_chat_setting(chat_id, 'suggestion_button_enabled', True) else 'выкл'}",
                    callback_data=f"admin_toggle:suggestion:{chat_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🔗 Приложение: {'вкл' if get_chat_setting(chat_id, 'app_button_enabled', True) else 'выкл'}",
                    callback_data=f"admin_toggle:app:{chat_id}",
                )
            ],
            [InlineKeyboardButton(text="📊 Статус", callback_data=f"admin_status:{chat_id}")],
        ]
    )


def build_welcome_markup(chat_id: int | None = None) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    if get_chat_setting(chat_id, 'app_button_enabled', True):
        buttons.append([InlineKeyboardButton(text="🚀 Открыть приложение", web_app=WebAppInfo(url="https://mortisplay.ru"))])
    buttons.append([InlineKeyboardButton(text="❓ Есть вопрос!🤓", callback_data="question_open")])
    buttons.append([InlineKeyboardButton(text="🆕 Обновление агента👀", callback_data="agent_updates_open")])
    if get_chat_setting(chat_id, 'suggestion_button_enabled', True):
        buttons.append([InlineKeyboardButton(text="💡 Кинуть предложку", callback_data="suggestion_open")])
    if not buttons:
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть приложение", web_app=WebAppInfo(url="https://mortisplay.ru"))]])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_suggestion_content(message: Message) -> str:
    text = (getattr(message, "text", None) or "").strip()
    caption = (getattr(message, "caption", None) or "").strip()

    if text and caption and text != caption:
        return f"{text}\n\n{caption}"
    if text:
        return text
    if caption:
        return caption
    return ""


def get_message_content(message: Message) -> str:
    text = (getattr(message, "text", None) or "").strip()
    caption = (getattr(message, "caption", None) or "").strip()

    if text and caption and text != caption:
        return f"{text}\n\n{caption}"
    return text or caption or ""


async def describe_photo_bytes(image_bytes: bytes) -> str:
    if not OPENAI_API_KEY:
        return ""

    try:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        response = await ai_client.chat.completions.create(
            model=MODEL_VISION,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Кратко опиши изображение на русском в 1–2 фразах, только по содержанию.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                        },
                    ],
                }
            ],
            max_tokens=40,
            temperature=0.2,
        )
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        content = getattr(getattr(choices[0], "message", {}), "content", None)
        if content:
            return str(content).strip()
    except Exception as exc:
        if is_openrouter_payment_required_error(exc):
            print("Ошибка распознавания фото: недостаточно средств на OpenRouter для изображения.")
        else:
            print(f"Ошибка распознавания фото: {exc}")
    return ""


async def send_suggestion_to_admin(message: Message, chat_id: int, suggestion_id: str, anonymous: bool) -> None:
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"suggestion_accept:{suggestion_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"suggestion_decline:{suggestion_id}"),
            ],
            [InlineKeyboardButton(text="💬 Комментарий", callback_data=f"suggestion_comment:{suggestion_id}")],
        ]
    )

    user = getattr(message, "from_user", None)
    full_name_parts = [
        part for part in [getattr(user, "first_name", None), getattr(user, "last_name", None)] if part
    ]
    full_name = " ".join(full_name_parts) if full_name_parts else "Пользователь"
    username = getattr(user, "username", None)
    user_id = getattr(user, "id", None)

    lines: list[str] = ["💡 Новая предложка"]
    if not anonymous and user_id is not None:
        line = f"От: {full_name}"
        if username:
            line += f" (@{username})"
        line += f"\nID: {user_id}"
        lines.append(line)

    caption_text = get_suggestion_content(message)
    if caption_text:
        lines.append("")
        lines.append(caption_text)

    base_text = "\n".join(lines).strip()

    try:
        if message.photo:
            await bot.send_photo(chat_id=chat_id, photo=message.photo[-1].file_id, caption=base_text, reply_markup=markup)
        elif message.video:
            await bot.send_video(chat_id=chat_id, video=message.video.file_id, caption=base_text, reply_markup=markup)
        elif message.voice:
            await bot.send_voice(chat_id=chat_id, voice=message.voice.file_id, caption=base_text, reply_markup=markup)
        elif message.audio:
            await bot.send_audio(chat_id=chat_id, audio=message.audio.file_id, caption=base_text, reply_markup=markup)
        elif message.document:
            await bot.send_document(chat_id=chat_id, document=message.document.file_id, caption=base_text, reply_markup=markup)
        else:
            await bot.send_message(chat_id=chat_id, text=base_text, reply_markup=markup)
    except Exception as exc:
        print(f"Ошибка отправки предложки админам: {exc}")
        traceback.print_exc()


async def send_question_to_admin(message: Message, chat_id: int, question_id: str) -> None:
    user = getattr(message, "from_user", None)
    full_name_parts = [
        part for part in [getattr(user, "first_name", None), getattr(user, "last_name", None)] if part
    ]
    full_name = " ".join(full_name_parts) if full_name_parts else "Пользователь"
    username = getattr(user, "username", None)
    user_id = getattr(user, "id", None)

    lines: list[str] = ["❓ Новый вопрос"]
    if user_id is not None:
        line = f"От: {full_name}"
        if username:
            line += f" (@{username})"
        line += f"\nID: {user_id}"
        lines.append(line)

    caption_text = get_suggestion_content(message)
    if caption_text:
        lines.append("")
        lines.append(caption_text)

    base_text = "\n".join(lines).strip()
    if not base_text:
        base_text = "❓ Новый вопрос"

    recipients = list(ADMIN_IDS) if ADMIN_IDS else [chat_id]
    for recipient_id in recipients:
        try:
            sent_message = await bot.send_message(
                chat_id=recipient_id,
                text=f"{base_text}\n\nОтветьте на это сообщение, чтобы отправить ответ пользователю.",
            )
            if sent_message:
                question_reply_targets[(int(recipient_id), int(sent_message.message_id))] = {
                    "user_id": int(user_id) if user_id is not None else None,
                    "chat_id": int(chat_id) if chat_id is not None else None,
                    "question_id": question_id,
                }
        except Exception as exc:
            print(f"Ошибка отправки вопроса админам: {exc}")
            traceback.print_exc()


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


load_quote_stats()
load_chat_settings()


async def generate_ai_reply(prompt_text: str, context_text: str | None = None) -> str:
    """Запрос к ИИ для общего ответа на вопрос (на русском)."""
    system_prompt = (
        "Ты — вежливый и полезный ассистент. Отвечай по-русски кратко и по существу, "
        "если просят — можешь дать небольшой совет или шаги. Не добавляй лишних пояснений."
    )
    if context_text:
        system_prompt = (
            f"{system_prompt}\n\n"
            "Ниже — дополнительный контекст о Mortisplay, его сайте и боте. "
            "Если пользователь спрашивает про это, отвечай на его основе; если нет — игнорируй этот контекст.\n"
            f"{context_text}"
        )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt_text},
    ]

    try:
        response = await ai_client.chat.completions.create(
            model=MODEL_CHAT,
            messages=messages,
            max_tokens=180,
            temperature=0.7,
            top_p=0.95,
            presence_penalty=0.2,
            frequency_penalty=0.2,
        )
        if not getattr(response, "choices", None):
            return prompt_text.strip()
        choice = response.choices[0]
        content = getattr(getattr(choice, "message", {}), "content", None)
        return content.strip() if content else prompt_text.strip()
    except Exception as e:
        if is_openrouter_access_denied_error(e):
            print("Ошибка AI reply: доступ к OpenRouter запрещён политикой безопасности.")
            return "Сейчас ИИ недоступен из-за ограничений доступа. Попробуйте позже."
        print(f"Ошибка AI reply: {e}")
        traceback.print_exc()
        return "Произошла ошибка при получении ответа от ИИ."


async def download_file_bytes(file_id: str) -> bytes:
    try:
        file = await bot.get_file(file_id)
        buffer = io.BytesIO()
        await bot.download_file(file.file_path, buffer)
        return buffer.getvalue()
    except Exception as exc:
        print(f"Ошибка загрузки файла из Telegram: {exc}")
        return b""


def guess_audio_format(mime_type: str | None) -> str:
    if not mime_type:
        return "ogg"
    mime_type = mime_type.lower()
    if mime_type.endswith("/opus"):
        return "ogg"
    if mime_type.endswith("/x-wav") or mime_type.endswith("/wav") or mime_type.endswith("/pcm"):
        return "wav"
    if mime_type.endswith("/mpeg") or mime_type.endswith("/mp3"):
        return "mp3"
    if mime_type.endswith("/x-flac") or mime_type.endswith("/flac"):
        return "flac"
    if mime_type.endswith("/x-m4a") or mime_type.endswith("/mp4"):
        return "mp4"
    if mime_type.endswith("/webm"):
        return "webm"
    if mime_type.endswith("/ogg"):
        return "ogg"
    if mime_type.endswith("/aac"):
        return "aac"
    return mime_type.split("/")[-1] or "ogg"


def is_openrouter_access_denied_error(error: Exception) -> bool:
    message = str(error).lower()
    if hasattr(error, "response") and error.response is not None:
        try:
            body_text = error.response.text
            if body_text:
                message += " " + body_text.lower()
        except Exception:
            pass
    return "access denied by security policy" in message or "error code: 403" in message or "403" in message


def is_openrouter_payment_required_error(error: Exception) -> bool:
    message = str(error).lower()
    if hasattr(error, "response") and error.response is not None:
        status_code = getattr(error.response, "status_code", None)
        if status_code == 402:
            return True
        try:
            body_text = error.response.text
            if body_text:
                message += " " + body_text.lower()
        except Exception:
            pass
    return "payment required" in message or "requires at least" in message or "balance" in message or "402" in message


async def transcribe_audio_bytes(audio_bytes: bytes, audio_format: str = "ogg") -> str:
    payload = {
        "model": MODEL_WHISPER,
        "input_audio": {
            "data": base64.b64encode(audio_bytes).decode("ascii"),
            "format": audio_format,
        },
        "language": "ru",
    }
    url = "https://openrouter.ai/api/v1/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "TelegramQuoteBot/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data.get("text", "").strip()
    except Exception as e:
        if is_openrouter_payment_required_error(e):
            print("Ошибка транскрипции: недостаточно средств на OpenRouter для аудио.")
            return ""
        print(f"Ошибка транскрипции: {e}")
        if hasattr(e, "response") and e.response is not None:
            try:
                print("Response body:", e.response.text)
            except Exception:
                pass
        traceback.print_exc()
        return ""


def is_video_update_request(text: str) -> bool:
    text = text.lower()
    has_video = any(word in text for word in ["видео", "ролик", "вылож", "анонс"])
    has_time_question = any(word in text for word in ["когда", "скоро", "когда новое", "когда выйдет", "когда будет"])
    return has_video and has_time_question


def get_quote_style(style: str | None) -> str:
    if style:
        normalized = style.lower().strip()
        if normalized in {"genz", "gen-z", "gen_z", "zumer", "zumerish", "зумер", "зумерский"}:
            return "genz"
        if normalized in {"roast", "toks", "дерзкий", "дерзко", "токсичный", "токсик"}:
            return "roast"
        if normalized in {"toxic", "slang", "зубастый", "саркастичный", "сленг", "жёсткий"}:
            return "toxic"
    return random.choice(["genz", "roast", "toxic"])


def infer_quote_style(source_text: str, command_text: str = "") -> str:
    combined = f"{source_text} {command_text}".lower()
    if any(word in combined for word in ["туп", "лентяй", "дебил", "мраз", "ху", "пизд", "слаб", "провал", "пьяный", "сдох", "ну и", "пустой", "бесполез"]):
        return "roast"
    if any(word in combined for word in ["вайб", "кринж", "мем", "лол", "бро", "чел", "окей", "ну", "серьёзно", "тип", "киш"]):
        return "genz"
    if any(word in combined for word in ["не смогу", "затянется", "надолго", "мда", "почему", "не выйду", "да?", "не могу", "серьёзно"]):
        return "toxic"
    return random.choice(["genz", "roast", "toxic"])


def select_relevant_messages(messages: list[str], max_items: int = 1) -> list[str]:
    if not messages:
        return []

    filtered: list[str] = []
    for item in messages:
        text = re.sub(r"\s+", " ", item).strip()
        if not text:
            continue
        if text.startswith("/"):
            continue
        filtered.append(text)

    if not filtered:
        return []
    if max_items <= 1:
        return [filtered[0]]
    return filtered[:max_items]


async def collect_reply_context(message: Message | None, max_messages: int) -> list[str]:
    if not message or max_messages <= 0:
        return []

    collected: list[str] = []
    seen_ids: set[int] = set()
    current = message

    while current and len(collected) < max_messages:
        msg_id = getattr(current, "message_id", None)
        if msg_id is None or msg_id in seen_ids:
            break
        seen_ids.add(msg_id)

        text = await extract_text_source(current)
        if text and text.strip():
            collected.append(text.strip())

        current = current.reply_to_message

    collected.reverse()
    return collected


def add_style_emoji(text: str, style: str | None) -> str:
    if not text:
        return text

    lowered = text.lower()
    if any(word in lowered for word in ["поёт", "поет", "петь", "хуёво", "плохо", "мусор", "криво"]):
        emoji = "🎤"
    elif any(word in lowered for word in ["ну и что", "и что", "ну", "да и", "ладно", "ок"]):
        emoji = "😏"
    elif any(word in lowered for word in ["не смогу", "затянется", "надолго", "не выйду", "не могу", "позже"]):
        emoji = "🫠"
    elif any(word in lowered for word in ["туп", "лентяй", "дебил", "мраз", "слаб", "провал", "пьяный"]):
        emoji = "💀"
    elif any(word in lowered for word in ["вайб", "мем", "кринж", "лол", "бро", "чел"]):
        emoji = "😂"
    else:
        suffixes = {
            "genz": ["😏", "🫠", "😂", "✨", "💅"],
            "roast": ["💀", "😂", "😭", "🙃", "🔥"],
            "toxic": ["😐", "😬", "💀", "🗿", "😏"],
        }
        chosen_style = get_quote_style(style)
        emoji = random.choice(suffixes.get(chosen_style, suffixes["roast"]))

    cleaned = re.sub(r"([\s\W])+$", "", text).strip()
    if not cleaned:
        return f"{text}{emoji}"
    return f"{cleaned} {emoji}"


def build_quote_reply(text: str, style: str | None) -> str:
    text = text.strip()
    if not text:
        return "Ничего не вижу, только тишина и твой вайб 😏"

    lowered = text.lower()
    if any(word in lowered for word in ["поёт", "поет", "петь", "поёт хуёво", "поёт плохо", "пение"]):
        return "Поёт как пьяный кот в микрофон 😂🐱💩"
    if any(word in lowered for word in ["не смогу", "затянется", "надолго", "не внесу", "не выйду", "не смогу это"]):
        return "Знаешь что отсутствует? Твоя мотивация, лентяй! 💀😂"

    chosen_style = get_quote_style(style)
    if chosen_style == "genz":
        templates = [
            "Серьёзно? Это звучит так, будто твой вайб на 1%, а не на 100% 😭",
            "Ты это сказал с таким лицом, будто у тебя зарядка на нуле, но ты всё ещё в режиме 'ну окей' 🫠",
            "Это не аргумент, это просто очень сильный 'я в процессе' вайб ✨",
            "Ты пишешь так, будто у тебя весь мир в 3 вкладках и одна из них — паника, а другая — мемы 😏",
            "Бро, это звучит так, будто ты вышел из комнаты и всё ещё пытаешься быть в теме 😂",
            "Ой, прям как будто ты в этом чате — главный герой, но только в своей голове, чел 😂",
            "Ну да, очень по-нашему: сначала шум, потом ноль смысла, и вот уже ты в роли главного героя 😏",
        ]
    elif chosen_style == "roast":
        templates = [
            "Ты это написал с такой уверенностью, будто у тебя есть план на жизнь. Но нет, тут только эмоции и шум 💀",
            "С таким подходом ты бы даже с тенью спорил и всё равно проиграл, мда 😂",
            "Это не мысль, это просто воздух с претензией на смысл 😏",
            "Ты не споришь — ты просто красиво шумишь, будто у тебя есть аргумент, но его нет 🙃",
            "Вот это прям очень смелый текст, если не считать, что он пришёл из пустоты, по факту 🫠",
            "С таким вайбом ты бы даже в зеркало посмотрел и всё равно нашёл повод для драматургии 😂",
            "Ну, это было очень смело, если не считать, что ты просто снова выдал пустой шум 😏",
        ]
    else:
        templates = [
            "С таким текстом ты не споришь — ты просто шумно исчезаешь из разговора 😐",
            "Мда, это звучит как попытка быть важным, но у тебя в запасе только vibes и пустота 😏",
            "Ты будто решил, что сарказм — это твой суперспособ, но на деле это просто фон с претензией 💀",
            "Вот это да, прямо как будто ты вышел из чата и всё ещё хочешь быть главным героем 😬",
            "Да, очень убедительно. Особенно если у тебя в голове нет ничего кроме этой фразы 😅",
            "Ну вот, снова эта попытка выглядеть острым, а по факту — просто смешно и скучно 😏",
            "Серьёзно, ты как будто решил, что пафос — это и есть характер, а по факту — просто шум 😐",
        ]
    return random.choice(templates)


def get_quote_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def is_ai_quote_message(message: Message | None) -> bool:
    if not message:
        return False

    markup = getattr(message, "reply_markup", None)
    if not markup:
        return False

    inline_keyboard = getattr(markup, "inline_keyboard", None)
    if not inline_keyboard:
        return False

    for row in inline_keyboard:
        for button in row:
            callback_data = getattr(button, "callback_data", None)
            if isinstance(callback_data, str) and callback_data.startswith("quote_"):
                return True
    return False


def ensure_quote_stats_entry(
    text: str,
    source_chat_id: int | None = None,
    source_message_id: int | None = None,
    source_user_id: int | None = None,
    source_username: str | None = None,
    source_chat_username: str | None = None,
) -> dict[str, Any]:
    quote_key = get_quote_key(text)
    entry = quote_stats.get(quote_key)
    if not entry:
        entry = {
            "text": text,
            "likes": 0,
            "dislikes": 0,
            "voters": {},
        }
        if source_chat_id is not None and source_message_id is not None:
            entry["source_chat_id"] = int(source_chat_id)
            entry["source_message_id"] = int(source_message_id)
        if source_user_id is not None:
            entry["source_user_id"] = int(source_user_id)
        if source_username:
            entry["source_username"] = str(source_username)
        if source_chat_username:
            entry["source_chat_username"] = str(source_chat_username)
        quote_stats[quote_key] = entry
    else:
        voters = entry.get("voters")
        if not isinstance(voters, dict):
            entry["voters"] = {}
        if (
            source_chat_id is not None
            and source_message_id is not None
            and entry.get("source_chat_id") is None
            and entry.get("source_message_id") is None
        ):
            entry["source_chat_id"] = int(source_chat_id)
            entry["source_message_id"] = int(source_message_id)
        if source_user_id is not None and entry.get("source_user_id") is None:
            entry["source_user_id"] = int(source_user_id)
        if source_username and not entry.get("source_username"):
            entry["source_username"] = str(source_username)
        if source_chat_username and not entry.get("source_chat_username"):
            entry["source_chat_username"] = str(source_chat_username)
    return entry


def format_quote_source(entry: dict[str, Any], bot_username: str | None = None) -> str:
    source_chat_id = entry.get("source_chat_id")
    source_message_id = entry.get("source_message_id")
    source_chat_username = entry.get("source_chat_username")
    if source_chat_id is None or source_message_id is None:
        return "Источник неизвестен"

    try:
        chat_id = int(source_chat_id)
        message_id = int(source_message_id)
    except (TypeError, ValueError):
        return "Источник неизвестен"

    def safe_username(value: str | None) -> str | None:
        if not value:
            return None
        cleaned = str(value).strip().lstrip("@")
        return cleaned or None

    normalized_chat_username = safe_username(source_chat_username)
    normalized_bot_username = safe_username(bot_username or BOT_USERNAME)

    if normalized_chat_username:
        url = f"https://t.me/{normalized_chat_username}/{message_id}"
        label = f"t.me/{normalized_chat_username}/{message_id}"
        return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'

    if chat_id > 0:
        # Private (personal) chat with the bot: there is no reliable per-message deep-link.
        # Provide a link to the bot profile (so user can open the dialog) and show message id as copyable code.
        if normalized_bot_username:
            bot_url = f"https://t.me/{normalized_bot_username}"
            label = f"Личный диалог с @{normalized_bot_username}"
            return f'<a href="{html.escape(bot_url, quote=True)}">{html.escape(label)}</a> · <code>сообщение №{message_id}</code>'
        return f"<code>Личное сообщение №{message_id}</code>"

    if chat_id < 0:
        url = f"https://t.me/c/{abs(chat_id)}/{message_id}"
        label = f"Чат #{abs(chat_id)} · сообщение #{message_id}"
        return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'

    return f"<code>Источник №{message_id}</code>"


def build_feedback_markup(text: str) -> InlineKeyboardMarkup:
    quote_key = get_quote_key(text)
    entry = quote_stats.get(quote_key) or ensure_quote_stats_entry(text)
    likes = int(entry.get("likes", 0))
    dislikes = int(entry.get("dislikes", 0))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"👍 {likes}", callback_data=f"quote_like:{quote_key}"),
                InlineKeyboardButton(text=f"👎 {dislikes}", callback_data=f"quote_dislike:{quote_key}"),
            ]
        ]
    )


def build_quote_display_text(text: str) -> str:
    return text.strip()


async def generate_quote_reply(text: str, style: str | None = None, command_text: str = "", chat_id: int | None = None) -> str:
    text = text.strip()
    if not text:
        return "Ничего не вижу, только тишина и твой вайб 😏"

    resolved_style = get_quote_style(style) if style else infer_quote_style(text, command_text)
    system_prompt = {
        "genz": "Ты — зумер-токсик из комментов Telegram. Пиши исключительно на современном русском молодежном сленге. Отвечай максимально вайбово, с легкой дерзостью, как в чате с корешами.",
        "roast": "Ты — мастер жесткого роаста и подколов. Твоя задача — едко высмеять сообщение пользователя, найти в нем уязвимость или глупость и разнести фактами с жестким юмором.",
        "toxic": "Ты — душный, саркастичный и максимально зубастый критик. Твои ответы режут как нож, наполнены чистым сарказмом и иронией.",
    }.get(resolved_style, "Ты — талантливый автор саркастичных и стильных reply-ответов в Telegram.")
    context_block = get_recent_chat_context(chat_id, limit=8) if chat_id is not None else ""
    prompt = (
        "Ты — автор очень живых, умных и остроумных reply-цитат для Telegram. "
        "Ты отлично понимаешь смысл сообщения, улавливаешь подтекст и отвечаешь так, чтобы это звучало ярко, "
        "с характером и с богатым словарным запасом. "
        f"Стиль ответа: {resolved_style}.\n"
        "Правила:\n"
        "- Пиши исключительно на современном русском молодежном сленге.\n"
        "- Отвечай живо, по-русски и без английских слов.\n"
        "- Держи ответ коротким: 1–2 предложения максимум.\n"
        "- Создавай ответы по смыслу сообщения и по интонации, а не по шаблону.\n"
        f"{context_block}\n" if context_block else ""
        f"Текст сообщения: {text}\n"
        "Верни только готовый ответ без объяснений и без кавычек."
    )

    try:
        response = await ai_client.chat.completions.create(
            model=MODEL_CHAT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            max_tokens=45,
            temperature=0.9,
            top_p=0.94,
            presence_penalty=0.2,
            frequency_penalty=0.2,
        )
        if not getattr(response, "choices", None):
            return build_quote_reply(text, resolved_style)
        choice = response.choices[0]
        content = getattr(getattr(choice, "message", {}), "content", None)
        reply_text = content.strip() if content else ""
        if reply_text:
            cleaned = re.sub(r"\s+", " ", reply_text).strip()
            if len(cleaned) > 2:
                return add_style_emoji(cleaned, resolved_style)
        return build_quote_reply(text, resolved_style)
    except Exception as e:
        if is_openrouter_access_denied_error(e):
            print("Ошибка генерации reply-цитаты: доступ к OpenRouter запрещён политикой безопасности.")
            return build_quote_reply(text, resolved_style)
        print(f"Ошибка генерации reply-цитаты: {e}")
        traceback.print_exc()
        return build_quote_reply(text, resolved_style)


async def extract_text_source(message: Message) -> str:
    text = (message.text or message.caption or "").strip()
    if text:
        return text

    return ""


async def send_quote_with_feedback(
    chat_id: int,
    reply_to_message_id: int | None,
    reply_text: str,
    source_chat_id: int | None = None,
    source_message_id: int | None = None,
    source_user_id: int | None = None,
    source_username: str | None = None,
    source_chat_username: str | None = None,
) -> None:
    ensure_quote_stats_entry(
        reply_text,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        source_user_id=source_user_id,
        source_username=source_username,
        source_chat_username=source_chat_username,
    )
    display_text = build_quote_display_text(reply_text)
    markup = build_feedback_markup(reply_text)
    try:
        if reply_to_message_id:
            await bot.send_message(chat_id=chat_id, text=display_text, reply_to_message_id=reply_to_message_id, reply_markup=markup)
        else:
            await bot.send_message(chat_id=chat_id, text=display_text, reply_markup=markup)
    except Exception as exc:
        print(f"Ошибка отправки AI-цитаты: {exc}")
        traceback.print_exc()


@dp.callback_query(lambda callback: callback.data and callback.data.startswith("admin_"))
async def handle_admin_callbacks(callback: CallbackQuery):
    if not is_admin_user(callback.from_user):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    data = callback.data or ""
    chat_id = getattr(callback.message, "chat", None).id if callback.message else None

    if data.startswith("admin_toggle:"):
        _, field, target_chat_id = data.split(":", 2)
        target_chat = int(target_chat_id) if target_chat_id.isdigit() else chat_id
        if field == "ai":
            new_value = not get_chat_setting(target_chat, "ai_enabled", True)
            set_chat_setting(target_chat, "ai_enabled", new_value)
            await callback.answer(f"ИИ-ответы {'включены' if new_value else 'выключены'}")
        elif field == "auto_quote":
            new_value = not get_chat_setting(target_chat, "auto_quote_enabled", True)
            set_chat_setting(target_chat, "auto_quote_enabled", new_value)
            await callback.answer(f"Автоцитаты {'включены' if new_value else 'выключены'}")
        elif field == "welcome":
            new_value = not get_chat_setting(target_chat, "welcome_enabled", True)
            set_chat_setting(target_chat, "welcome_enabled", new_value)
            await callback.answer(f"Приветствие {'включено' if new_value else 'выключено'}")
        elif field == "suggestion":
            new_value = not get_chat_setting(target_chat, "suggestion_button_enabled", True)
            set_chat_setting(target_chat, "suggestion_button_enabled", new_value)
            await callback.answer(f"Кнопка предложки {'включена' if new_value else 'выключена'}")
        elif field == "app":
            new_value = not get_chat_setting(target_chat, "app_button_enabled", True)
            set_chat_setting(target_chat, "app_button_enabled", new_value)
            await callback.answer(f"Кнопка приложения {'включена' if new_value else 'выключена'}")
    else:
        await callback.answer("Статус обновлён")

    if callback.message:
        try:
            await callback.message.edit_text(
                "Админ-панель\n\n"
                f"• AI ответы: {'вкл' if get_chat_setting(chat_id, 'ai_enabled', True) else 'выкл'}\n"
                f"• Автоцитаты: {'вкл' if get_chat_setting(chat_id, 'auto_quote_enabled', True) else 'выкл'}\n"
                f"• Приветствие: {'вкл' if get_chat_setting(chat_id, 'welcome_enabled', True) else 'выкл'}\n"
                f"• Предложка: {'вкл' if get_chat_setting(chat_id, 'suggestion_button_enabled', True) else 'выкл'}\n"
                f"• Приложение: {'вкл' if get_chat_setting(chat_id, 'app_button_enabled', True) else 'выкл'}",
                reply_markup=build_admin_markup(chat_id or int(callback.message.chat.id)),
            )
        except Exception:
            pass


@dp.callback_query(lambda callback: callback.data == "question_open")
async def handle_question_open(callback: CallbackQuery):
    if not callback.from_user:
        await callback.answer("Не удалось начать вопрос.")
        return

    pending_questions[callback.from_user.id] = callback.message.chat.id if callback.message else 0
    await callback.answer("Отправьте ваш вопрос.")
    try:
        await callback.message.answer("Напишите ваш вопрос — я передам его администратору.")
    except Exception:
        pass


@dp.callback_query(lambda callback: callback.data == "agent_updates_open")
async def handle_agent_updates(callback: CallbackQuery):
    await callback.answer("Смотрите, что новенького в агенте")
    try:
        await callback.message.answer(AGENT_UPDATES_TEXT)
    except Exception:
        pass


@dp.callback_query(lambda callback: callback.data and callback.data.startswith("suggestion_"))
async def handle_suggestion_callbacks(callback: CallbackQuery):
    data = callback.data or ""
    if data.startswith("suggestion_mode:"):
        if not callback.from_user:
            await callback.answer("Не удалось сохранить выбор.")
            return
        mode = data.split(":", 1)[1]
        pending_suggestions[callback.from_user.id] = callback.message.chat.id if callback.message else 0
        suggestion_anonymity[callback.from_user.id] = mode == "anon"
        await callback.answer("Окей. Теперь отправьте текст или медиа.")
        try:
            await callback.message.answer("Отправьте текст, фото, видео или голосовое — админ увидит это и решит, принимать или отклонять.")
        except Exception:
            pass
        return

    if data == "suggestion_open":
        if not callback.from_user:
            await callback.answer("Не удалось начать предложение.")
            return
        pending_suggestions.pop(callback.from_user.id, None)
        suggestion_anonymity[callback.from_user.id] = False
        await callback.answer("Выберите формат отправки.")
        try:
            await callback.message.answer(
                "Отправить предложку анонимно?",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="🕵️ Анонимно", callback_data="suggestion_mode:anon"),
                            InlineKeyboardButton(text="👤 Обычная", callback_data="suggestion_mode:public"),
                        ]
                    ]
                ),
            )
        except Exception:
            pass
        return

    if data.startswith("suggestion_accept:"):
        suggestion_id = data.split(":", 1)[1]
        entry = suggestion_requests.get(suggestion_id)
        if entry:
            await callback.answer("Предложка принята.")
            try:
                await callback.message.edit_text(f"✅ Принято\n\n{entry['text']}", reply_markup=None)
            except Exception:
                pass
            if entry.get("user_id"):
                try:
                    await bot.send_message(chat_id=entry["user_id"], text="✅ Ваша предложка принята.")
                except Exception:
                    pass
        return

    if data.startswith("suggestion_decline:"):
        suggestion_id = data.split(":", 1)[1]
        entry = suggestion_requests.get(suggestion_id)
        if entry:
            await callback.answer("Предложка отклонена.")
            try:
                await callback.message.edit_text(f"❌ Отклонено\n\n{entry['text']}", reply_markup=None)
            except Exception:
                pass
            if entry.get("user_id"):
                try:
                    await bot.send_message(chat_id=entry["user_id"], text="❌ Ваша предложка отклонена.")
                except Exception:
                    pass
        return

    if data.startswith("suggestion_comment:"):
        suggestion_id = data.split(":", 1)[1]
        pending_admin_comments[callback.from_user.id] = suggestion_id
        await callback.answer("Напишите комментарий.")
        try:
            await callback.message.answer("Введите комментарий к предложке.")
        except Exception:
            pass


@dp.callback_query(lambda callback: callback.data and callback.data.startswith("quote_"))
async def handle_quote_feedback(callback: CallbackQuery):
    data = callback.data or ""
    if not data.startswith("quote_"):
        return

    action, quote_key = data.split(":", 1) if ":" in data else (data, "")
    if not quote_key:
        await callback.answer("Не удалось сохранить оценку.")
        return

    entry = quote_stats.get(quote_key)
    if entry is None:
        entry = {"text": "", "likes": 0, "dislikes": 0, "voters": {}}
        quote_stats[quote_key] = entry

    voters = entry.get("voters")
    if not isinstance(voters, dict):
        voters = {}
        entry["voters"] = voters

    user_id = getattr(callback.from_user, "id", None)
    if user_id is not None:
        user_key = str(user_id)
        if user_key in voters:
            await callback.answer("Вы уже оценили эту цитату.")
            return

    if action == "quote_like":
        entry["likes"] = int(entry.get("likes", 0)) + 1
        if user_id is not None:
            voters[str(user_id)] = "like"
    elif action == "quote_dislike":
        entry["dislikes"] = int(entry.get("dislikes", 0)) + 1
        if user_id is not None:
            voters[str(user_id)] = "dislike"

    save_quote_stats()
    if callback.message and entry.get("text"):
        await callback.message.edit_text(
            build_quote_display_text(str(entry.get("text", ""))),
            reply_markup=build_feedback_markup(str(entry.get("text", ""))),
        )
    await callback.answer("Оценка сохранена")


@dp.message(Command(commands=["admin"], ignore_case=True))
async def handle_admin_panel(message: Message):
    if not is_admin_user(message.from_user):
        await message.reply("Нет доступа.")
        return
    await message.reply("Админ-панель", reply_markup=build_admin_markup(message.chat.id))


@dp.message(Command(commands=["start"], ignore_case=True))
async def handle_start(message: Message):
    if not get_chat_setting(message.chat.id, 'welcome_enabled', True):
        return
    await message.answer(WELCOME_TEXT, reply_markup=build_welcome_markup(message.chat.id))


@dp.message(Command(commands=["help"], ignore_case=True))
async def handle_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=build_welcome_markup(message.chat.id))


@dp.message(Command(commands=["q"], ignore_case=True))
async def handle_quote_command(message: Message):
    text = (message.text or "").strip()
    args = text.split()[1:] if text.startswith("/q") else []

    replied = message.reply_to_message
    if not replied:
        await message.reply("Ответь на сообщение, чтобы я сделал из него цитату.")
        return

    count = 1
    explicit_style = None

    if args:
        first_arg = args[0].strip()
        if first_arg.isdigit():
            count = max(1, int(first_arg))
            explicit_style = args[1].strip() if len(args) > 1 else None
        else:
            explicit_style = first_arg

    source_text = ""
    if replied.voice or replied.audio:
        file_id = getattr(replied.voice, "file_id", None) or getattr(replied.audio, "file_id", None)
        if file_id:
            audio_bytes = await download_file_bytes(file_id)
            if audio_bytes:
                audio_format = guess_audio_format(getattr(replied.voice, "mime_type", None) or getattr(replied.audio, "mime_type", None))
                source_text = await transcribe_audio_bytes(audio_bytes, audio_format)
    elif replied.photo:
        photo = max(replied.photo, key=lambda item: (getattr(item, "width", 0) or 0) * (getattr(item, "height", 0) or 0))
        if photo:
            photo_bytes = await download_file_bytes(photo.file_id)
            if photo_bytes:
                source_text = await describe_photo_bytes(photo_bytes)
    else:
        source_texts = await collect_reply_context(replied, max_messages=max(count + 3, 3))
        if not source_texts:
            await message.reply("Не нашёл ни одного текстового сообщения для объединения.")
            return
        relevant_messages = select_relevant_messages(source_texts, max_items=max(count, 1))
        if not relevant_messages:
            relevant_messages = source_texts[: max(count, 1)]
        source_text = "\n".join(relevant_messages)

    if not source_text.strip():
        await message.reply("Не нашёл подходящего текста для цитаты.")
        return

    resolved_style = get_quote_style(explicit_style) if explicit_style else infer_quote_style(source_text, text)
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except Exception as exc:
        print(f"Ошибка отправки typing action: {exc}")

    reply_text = await generate_quote_reply(source_text, resolved_style, text, chat_id=message.chat.id)
    await send_quote_with_feedback(
        message.chat.id,
        message.message_id,
        reply_text,
        source_chat_id=replied.chat.id,
        source_message_id=replied.message_id,
        source_user_id=getattr(replied.from_user, "id", None),
        source_username=getattr(replied.from_user, "username", None) or getattr(replied.from_user, "first_name", None),
        source_chat_username=getattr(replied.chat, "username", None),
    )


@dp.message(Command(commands=["topquotes", "top"], ignore_case=True))
async def handle_top_quotes(message: Message):
    rated_quotes = [
        item
        for item in quote_stats.values()
        if int(item.get("likes", 0)) > 0 or int(item.get("dislikes", 0)) > 0
    ]
    ranked = sorted(
        rated_quotes,
        key=lambda item: (
            int(item.get("likes", 0)) - int(item.get("dislikes", 0)),
            int(item.get("likes", 0)),
        ),
        reverse=True,
    )[:5]

    if not ranked:
        await message.reply("Пока никто не оценивал цитаты. Лайкай и собирай топ!")
        return

    lines = []
    for idx, item in enumerate(ranked):
        text = html.escape(str(item.get("text", "—")))
        likes = int(item.get("likes", 0))
        dislikes = int(item.get("dislikes", 0))
        source = format_quote_source(item, BOT_USERNAME)
        lines.append(
            f"{idx + 1}. <b>{text}</b> — 👍 {likes} 👎 {dislikes}\nИсточник: {source}"
        )

    await message.reply("Топ 5 оценённых цитат:\n" + "\n\n".join(lines), parse_mode="HTML")


def is_private_chat(message: Message) -> bool:
    chat = getattr(message, "chat", None)
    if chat is None:
        return False
    chat_type = getattr(chat, "type", None)
    if chat_type == "private":
        return True
    chat_id = getattr(chat, "id", None)
    return bool(chat_id and int(chat_id) > 0)


def get_private_chat_template(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not normalized:
        return None

    if any(phrase in normalized for phrase in ["кто такой mortisplay", "расскажи про mortisplay", "про ютуб", "кто такой ютубер", "кто такой mortis"]):
        return (
            "Mortisplay — это ютубер, который занимается записью видеоигр, выкладывает угары, баги и эпики, а также снимает ролики вместе с друзьями, особенно rol1j, Johnny_Drill и другими. "
            "Он уже больше 5 лет пытается добиться успеха, но мир не даёт ему покоя, чтобы он достиг своих 1К подписчиков и в какой-то момент нашёл свою славу. "
            "За кулисами он также снимает разные видеоролики, гайды, подкасты и анимации — раньше больше этим занимался, сейчас уже не так часто. "
            "У него есть 3 канала: Mortisplay32 (основной), Mortisplay_Studio (дополнительный) и F.U.J.I.N.mk56 (старый и заброшенный контент по SO2). "
            "Если тебе было интересно послушать про разработчика этого агента, то приходи на его Telegram-канал: https://t.me/MortisPlayTG\n\n"
            "Спасибо за внимание ❤️"
        )

    if any(phrase in normalized for phrase in ["что за сайт mortisplay.ru", "что за сайт", "про сайт", "про mortisplay.ru", "сайт mortisplay"]):
        return (
            "mortisplay.ru — это новый высокий уровень в карьере Mortis'a, где есть много развлекательного контента с ютуба и много крутых кнопочек. "
            "В основном Mortis создавал его для своей аудитории, но пользовались им не так часто, потому что тогда у него была ещё маленькая аудитория. "
            "Но уже сайту исполнился 1 год, и он стал частью семейной карьеры Mortis'a ❤️ "
            "На сайте есть разделы Видео, Twitch и другие разделы. Всё остальное можно посмотреть прямо на сайте mortisplay.ru.\n\n"
            "Спасибо за внимание ❤️"
        )

    if any(phrase in normalized for phrase in ["что за агент", "зачем создан агент", "для чего бот", "что это за агент", "что за бот"]):
        return (
            "Привет! Спасибо, что задал такой вопрос, но всё это лучше расскажет разработчик, потому что он в этом понимает больше: "
            "По сути, это обычный бот, но мы решили сделать его в агента, потому что он оснащён хорошим искусственным интеллектом. "
            "Через него можно делать ИИ-цитаты в группах, а также смотреть наш сайт прямо в Telegram через бота-агента. "
            "Он ещё может иногда работать неидеально, потому что это первый стабильный бот в Telegram, который разработчик когда-либо делал. "
            "В основном его делали для ИИ-цитат в чате, но в итоге он может стать самым развитым ботом-агентом."
        )

    if any(phrase in normalized for phrase in ["зачем он придумал ник mortis", "почему ник mortis", "почему mortis", "зачем придумал ник"]):
        return (
            "Хо хо хо, хороший вопрос, на котором сам Mortis затрудняется ответить. "
            "Но всё очень просто: он просто придумал этот ник в голове, сплагиатил его из собственного вдохновения и так появился на свет. "
            "При этом он ничего не украл, ни у Бравла, ни у кого-то другого."
        )

    return None


@dp.message()
async def handle_general_templates(message: Message):
    text = get_message_content(message).strip()
    has_media = any([message.photo, message.video, message.video_note, message.voice, message.audio, message.document])
    if message.from_user and not message.from_user.is_bot and message.text and not message.text.startswith("/"):
        save_message_to_history(message.chat.id, message.text)
    if not text and not has_media:
        return

    if message.text and message.text.startswith("/"):
        return
    if message.from_user and message.from_user.is_bot:
        return

    if message.from_user and message.from_user.id in pending_suggestions:
        chat_id = pending_suggestions.pop(message.from_user.id)
        anonymous = suggestion_anonymity.pop(message.from_user.id, False)
        suggestion_id = hashlib.sha256(f"{message.from_user.id}:{time.time()}".encode("utf-8")).hexdigest()[:10]
        suggestion_requests[suggestion_id] = {
            "text": get_suggestion_content(message),
            "user_id": message.from_user.id,
            "chat_id": chat_id,
            "anonymous": anonymous,
        }
        await send_suggestion_to_admin(message, chat_id if chat_id else message.chat.id, suggestion_id, anonymous)
        await message.reply("Предложка отправлена админам.")
        return

    if message.from_user and message.from_user.id in pending_questions:
        pending_questions.pop(message.from_user.id, None)
        question_id = hashlib.sha256(f"{message.from_user.id}:{time.time()}".encode("utf-8")).hexdigest()[:10]
        await send_question_to_admin(message, message.chat.id, question_id)
        await message.reply("Вопрос отправлен администратору.")
        return

    if message.from_user and is_admin_user(message.from_user) and message.reply_to_message:
        reply_target = question_reply_targets.get((message.chat.id, message.reply_to_message.message_id))
        if reply_target is not None:
            reply_text = get_message_content(message).strip()
            if reply_text:
                user_id = reply_target.get("user_id")
                try:
                    if user_id is not None:
                        await bot.send_message(chat_id=int(user_id), text=f"💬 Ответ от админа:\n\n{reply_text}")
                    else:
                        await message.reply("Не удалось определить пользователя для ответа.")
                except Exception:
                    pass
            question_reply_targets.pop((message.chat.id, message.reply_to_message.message_id), None)
            await message.reply("Ответ отправлен пользователю.")
            return

    if message.from_user and message.from_user.id in pending_admin_comments:
        suggestion_id = pending_admin_comments.pop(message.from_user.id)
        entry = suggestion_requests.get(suggestion_id)
        if entry:
            text = message.text or message.caption or ""
            try:
                await bot.send_message(chat_id=entry["user_id"], text=f"💬 Комментарий от админа:\n\n{text}")
            except Exception:
                pass
            await message.reply("Комментарий отправлен пользователю.")
        return

    if is_private_chat(message):
        template = get_private_chat_template(text)
        if template:
            await message.reply(template)
            return

    global BOT_USERNAME
    if not BOT_USERNAME:
        try:
            me = await bot.get_me()
            BOT_USERNAME = me.username if me and me.username else None
        except Exception:
            BOT_USERNAME = None

    if get_chat_setting(message.chat.id, 'ai_enabled', True) and BOT_USERNAME and f"@{BOT_USERNAME.lower()}" in text.lower():
        clean_text = re.sub(rf"@{re.escape(BOT_USERNAME)}", "", text, flags=re.I).strip()
        if not clean_text:
            await message.reply("Да? Чем помочь? Напишите вопрос после упоминания бота.")
            return

        context_text = get_private_chat_template(clean_text)
        try:
            await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        except Exception as exc:
            print(f"Ошибка отправки typing action: {exc}")
        status = await message.reply("🤖 Думаю...")
        try:
            ai_resp = await generate_ai_reply(clean_text, context_text=context_text)
            try:
                await status.edit_text(ai_resp)
            except Exception:
                await message.reply(ai_resp)
        except Exception as e:
            print(f"Ошибка при ответе ИИ на упоминание: {e}")
            traceback.print_exc()
            try:
                await status.edit_text("Ошибка при получении ответа от ИИ.")
            except Exception:
                await message.reply("Ошибка при получении ответа от ИИ.")
        return

    if is_video_update_request(text):
        answers = [
            "Бро, новое видео уже в работе. Следи за анонсом на канале, не пропустишь 🔥",
            "Когда будет — тогда и будет. Анонс обязательно появится у нас в канале)",
            "Терпение, брат. Всё выйдет, когда будет готово. Следи за анонсами)",
            "Я бы сказал точную дату, но тогда Mortis меня прибьёт 😂 Жди анонс на канале",
            "Скоро, уже пахнет новым видосом. Анонс будет — не прогляди!",
            "Новое видео в процессе. Чтобы не ждать в пустоту — подписан на канал? Там всё будет)",
            "Бро, не дави, дай нам пожарить контент как следует. Анонс на канале будет первым делом",
            "Когда рак на горе свистнет... или когда анонс выйдет — как повезёт 😏 Следи за каналом",
        ]
        await message.reply(random.choice(answers))
        return

    reply_to_ai_quote = bool(message.reply_to_message and is_ai_quote_message(message.reply_to_message))
    should_generate_quote = reply_to_ai_quote or (
        get_chat_setting(message.chat.id, 'auto_quote_enabled', True) and random.random() < 0.08
    )
    if should_generate_quote:
        try:
            source_text = text or await extract_text_source(message)
            if not source_text.strip():
                return
            try:
                await bot.send_chat_action(chat_id=message.chat.id, action="typing")
            except Exception as exc:
                print(f"Ошибка отправки typing action: {exc}")
            reply_text = await generate_quote_reply(source_text, None, source_text)
            await send_quote_with_feedback(
                message.chat.id,
                None,
                reply_text,
                source_chat_id=message.chat.id,
                source_message_id=message.message_id,
                source_user_id=getattr(message.from_user, "id", None),
                source_username=getattr(message.from_user, "username", None) or getattr(message.from_user, "first_name", None),
                source_chat_username=getattr(message.chat, "username", None),
            )
        except Exception as e:
            print(f"Ошибка генерации AI-цитаты: {e}")
            traceback.print_exc()


# --- ЗАПУСК ---
async def main():
    print("Бот запущен на OpenRouter с дизайном QuotLy! Погнали!")
    global BOT_USERNAME
    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username if me and me.username else None
        print(f"Bot username: @{BOT_USERNAME}" if BOT_USERNAME else "Bot username неизвестен")
    except Exception as e:
        print("Не удалось получить username бота:", e)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())