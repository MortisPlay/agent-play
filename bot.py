import asyncio
import base64
import hashlib
import html
import io
import json
import os
import random
import re
import time
import traceback
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from aiogram.types.input_file import BufferedInputFile
from openai import AsyncOpenAI
import httpx

# Load env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FONT_PATH = os.getenv("FONT_PATH", "Roboto-Bold.ttf")
BOT_USERNAME: str | None = None
DATA_DIR = Path(__file__).resolve().parent
STATS_PATH = DATA_DIR / "quote_stats.json"
CHAT_SETTINGS_PATH = DATA_DIR / "chat_settings.json"
ADMIN_IDS = {int(item) for item in os.getenv("ADMIN_IDS", "").split(",") if item.strip().isdigit()}
AUTO_QUOTE_ENABLED = True
AI_RESPONSE_ENABLED = True
chat_settings: dict[str, dict[str, Any]] = {}
pending_suggestions: dict[int, int] = {}
pending_admin_comments: dict[int, str] = {}
suggestion_anonymity: dict[int, bool] = {}

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

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except Exception:  # pragma: no cover - optional dependency for stickers
    Image = None
    ImageDraw = None
    ImageFont = None
    ImageOps = None


def is_admin_user(user: Any | None) -> bool:
    if not user:
        return False
    if not ADMIN_IDS:
        return True
    return int(getattr(user, "id", 0)) in ADMIN_IDS


def load_chat_settings() -> dict[str, dict[str, Any]]:
    global chat_settings
    if CHAT_SETTINGS_PATH.exists():
        try:
            with CHAT_SETTINGS_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                chat_settings = data
        except Exception:
            chat_settings = {}
    else:
        chat_settings = {}
    return chat_settings


def save_chat_settings() -> None:
    with CHAT_SETTINGS_PATH.open("w", encoding="utf-8") as handle:
        json.dump(chat_settings, handle, ensure_ascii=False, indent=2)


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
    if get_chat_setting(chat_id, 'suggestion_button_enabled', True):
        buttons.append([InlineKeyboardButton(text="💡 Кинуть предложку", callback_data="suggestion_open")])
    if not buttons:
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть приложение", web_app=WebAppInfo(url="https://mortisplay.ru"))]])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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

    caption_text = message.text or message.caption or ""
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


def load_quote_stats() -> dict[str, dict[str, Any]]:
    global quote_stats
    if STATS_PATH.exists():
        try:
            with STATS_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                quote_stats = data
        except Exception:
            quote_stats = {}
    else:
        quote_stats = {}
    return quote_stats


def save_quote_stats() -> None:
    with STATS_PATH.open("w", encoding="utf-8") as handle:
        json.dump(quote_stats, handle, ensure_ascii=False, indent=2)


load_quote_stats()
load_chat_settings()


async def generate_ai_reply(prompt_text: str) -> str:
    """Запрос к ИИ для общего ответа на вопрос (на русском)."""
    messages = [
        {
            "role": "system",
            "content": (
                "Ты — вежливый и полезный ассистент. Отвечай по-русски кратко и по существу, "
                "если просят — можешь дать небольшой совет или шаги. Не добавляй лишних пояснений."
            ),
        },
        {"role": "user", "content": prompt_text},
    ]

    try:
        response = await ai_client.chat.completions.create(
            model="meta-llama/llama-3.1-8b-instruct",
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
    file = await bot.get_file(file_id)
    buffer = io.BytesIO()
    await bot.download_file(file.file_path, buffer)
    return buffer.getvalue()


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


async def transcribe_audio_bytes(audio_bytes: bytes, audio_format: str = "ogg") -> str:
    payload = {
        "model": "openai/whisper-large-v3",
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
        lowered = text.lower()
        if len(text) <= 3:
            continue
        if any(marker in lowered for marker in ["http://", "https://", "@", "/q", "/start", "/help", "/qs"]):
            continue
        if text.startswith("/"):
            continue
        filtered.append(text)

    if not filtered:
        return messages[:max_items]

    if max_items <= 1:
        return [filtered[0]]

    selected = []
    for text in filtered:
        if len(text) >= 8 or len(selected) < 1:
            selected.append(text)
        if len(selected) >= max_items:
            break
    if len(selected) < max_items:
        selected.extend(filtered[: max_items - len(selected)])
    return selected


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
    return entry


def format_quote_source(entry: dict[str, Any]) -> str:
    source_chat_id = entry.get("source_chat_id")
    source_message_id = entry.get("source_message_id")
    if source_chat_id is None or source_message_id is None:
        return "Источник неизвестен"

    try:
        chat_id = int(source_chat_id)
    except (TypeError, ValueError):
        return "Источник неизвестен"

    if chat_id < 0:
        return f"https://t.me/c/{abs(chat_id)}/{source_message_id}"

    return f"Личное сообщение#{source_message_id}"


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


async def generate_quote_reply(text: str, style: str | None = None, command_text: str = "") -> str:
    text = text.strip()
    if not text:
        return "Ничего не вижу, только тишина и твой вайб 😏"

    resolved_style = get_quote_style(style) if style else infer_quote_style(text, command_text)
    prompt = (
        "Ты — автор очень живых, умных и остроумных reply-цитат для Telegram. "
        "Ты отлично понимаешь смысл сообщения, улавливаешь подтекст и отвечаешь так, чтобы это звучало ярко, "
        "с характером и с богатым словарным запасом. "
        f"Стиль ответа: {resolved_style}.\n"
        "Правила:\n"
        "- Отвечай ТОЛЬКО на русском языке. Никакого английского, никаких чужих языков.\n"
        "- Если стиль genz — отвечай современно, вайбово, с лёгкой дерзостью и живым сленгом, будто ты в чате с очень уверенным пацаном.\n"
        "- Если стиль roast — отвечай дерзко, с подколом, едко, но не тупо; держи удар, но не превращайся в бессмысленную агрессию.\n"
        "- Если стиль toxic — отвечай жёстко, саркастично, зубасто, с очень цепляющей подачей, будто ты режешь фразу как ножом.\n"
        "- Делай ответы с юмором, шуткой, подколом и живой речью. Не будь сухим.\n"
        "- Используй лёгкие характерные слова вроде 'бро', 'чел', 'вайб', 'мда', 'ну', 'по факту' — но умеренно, чтобы не звучать нелепо.\n"
        "- Не делай слишком длинный ответ. 1–2 предложения максимум.\n"
        "- Не используй шаблонные фразы, делай ответ по смыслу сообщения и по интонации.\n"
        "- Сохраняй атмосферу Telegram, но с хорошей речью, характером и вкусом.\n"
        "- Если в сообщении есть провокация, слабость, попытка выглядеть важным или вайб — отвечай с подколом.\n"
        "- Если есть самоирония, неловкость или абсурд — отвечай веселее и проще.\n"
        "- Используй смайлики, но естественно, без перебора.\n"
        f"Текст сообщения: {text}\n"
        "Верни только готовый ответ без объяснений и без кавычек."
    )

    try:
        response = await ai_client.chat.completions.create(
            model="meta-llama/llama-3.1-8b-instruct",
            messages=[
                {"role": "system", "content": "Ты — талантливый автор саркастичных и стильных reply-ответов в Telegram."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=70,
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

    file_id = None
    mime_type = None
    if message.voice:
        file_id = message.voice.file_id
        mime_type = message.voice.mime_type
    elif message.audio:
        file_id = message.audio.file_id
        mime_type = message.audio.mime_type
    elif message.video_note:
        file_id = message.video_note.file_id
        mime_type = message.video_note.mime_type
    elif message.video:
        file_id = message.video.file_id
        mime_type = message.video.mime_type
    elif message.document and message.document.mime_type:
        if message.document.mime_type.startswith("audio/") or message.document.mime_type.startswith("video/"):
            file_id = message.document.file_id
            mime_type = message.document.mime_type

    if file_id:
        try:
            audio_bytes = await download_file_bytes(file_id)
            audio_format = guess_audio_format(mime_type)
            transcript = await transcribe_audio_bytes(audio_bytes, audio_format)
            return transcript
        except Exception as e:
            print(f"Ошибка получения/транскрипции медиа: {e}")
            traceback.print_exc()
    return ""


async def get_user_avatar_bytes(user_id: int | None) -> bytes | None:
    if not user_id or not bot:
        return None
    try:
        photos = await bot.get_user_profile_photos(user_id)
        if not getattr(photos, "photos", None) or not photos.photos:
            return None
        file_id = photos.photos[0][0].file_id
        file = await bot.get_file(file_id)
        buffer = io.BytesIO()
        await bot.download_file(file.file_path, buffer)
        return buffer.getvalue()
    except Exception as exc:
        print(f"Ошибка загрузки аватара: {exc}")
        return None


async def create_sticker_bytes(text: str, user_photo: bytes | None = None, username: str | None = None) -> bytes:
    if Image is None or ImageDraw is None or ImageFont is None:
        raise RuntimeError("Pillow не установлен")

    width, height = 512, 512
    image = Image.new("RGBA", (width, height), (16, 20, 30, 255))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((20, 20, width - 20, height - 20), radius=36, fill=(24, 28, 42, 255), outline=(87, 102, 129, 255), width=3)
    draw.rounded_rectangle((34, 34, width - 34, height - 34), radius=28, fill=(12, 16, 25, 255))
    draw.rectangle((36, 34, width - 36, 70), fill=(255, 107, 107, 255))

    font_path = None
    if os.path.exists(FONT_PATH):
        font_path = FONT_PATH
    elif os.path.exists(str(DATA_DIR / FONT_PATH)):
        font_path = str(DATA_DIR / FONT_PATH)

    font_title = None
    font_body = None
    if font_path:
        try:
            font_title = ImageFont.truetype(font_path, 28)
            font_body = ImageFont.truetype(font_path, 30)
        except Exception:
            font_title = ImageFont.load_default()
            font_body = ImageFont.load_default()
    if font_title is None:
        font_title = ImageFont.load_default()
    if font_body is None:
        font_body = ImageFont.load_default()

    if user_photo:
        try:
            avatar = Image.open(io.BytesIO(user_photo)).convert("RGBA").resize((96, 96))
            mask = Image.new("L", avatar.size, 0)
            avatar_draw = ImageDraw.Draw(mask)
            avatar_draw.ellipse((0, 0, avatar.size[0] - 1, avatar.size[1] - 1), fill=255)
            avatar.putalpha(mask)
            image.alpha_composite(avatar, dest=(44, 86))
            draw.ellipse((44, 86, 44 + 96, 86 + 96), outline=(255, 255, 255, 255), width=3)
        except Exception:
            pass

    label = "QuotLy"
    draw.text((150, 62), label, fill=(255, 255, 255, 255), font=font_title)

    display_name = (username or "anonymous").strip()
    if display_name and not display_name.startswith("@"):
        display_name = f"@{display_name}"
    if not display_name:
        display_name = "@anonymous"
    display_name = display_name[:22]
    draw.text((150, 96), display_name, fill=(188, 213, 255, 255), font=font_title)

    quote_text = re.sub(r"\s+", " ", text).strip() or ""
    if not quote_text:
        quote_text = "Ничего не вижу, только тишина..."

    max_width = width - 120
    words = quote_text.split()
    lines: list[str] = []
    current_line = ""
    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font_body)
        if bbox[2] <= max_width or not current_line:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    if not lines:
        lines = [quote_text[:40]]

    start_y = 152
    line_height = 42
    for index, line in enumerate(lines[:5]):
        y = start_y + index * line_height
        draw.text((44, y), line, fill=(255, 255, 255, 255), font=font_body)

    output = io.BytesIO()
    image.save(output, format="WEBP")
    return output.getvalue()


async def send_quote_with_feedback(
    chat_id: int,
    reply_to_message_id: int | None,
    reply_text: str,
    source_chat_id: int | None = None,
    source_message_id: int | None = None,
    source_user_id: int | None = None,
    source_username: str | None = None,
) -> None:
    ensure_quote_stats_entry(
        reply_text,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        source_user_id=source_user_id,
        source_username=source_username,
    )
    display_text = build_quote_display_text(reply_text)
    markup = build_feedback_markup(reply_text)
    if reply_to_message_id:
        await bot.send_message(chat_id=chat_id, text=display_text, reply_to_message_id=reply_to_message_id, reply_markup=markup)
    else:
        await bot.send_message(chat_id=chat_id, text=display_text, reply_markup=markup)


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


@dp.callback_query(lambda callback: callback.data and callback.data.startswith("suggestion_"))
async def handle_suggestion_callbacks(callback: CallbackQuery):
    data = callback.data or ""
    if data.startswith("suggestion_mode:"):
        if not callback.from_user:
            await callback.answer("Не удалось сохранить выбор.")
            return
        mode = data.split(":", 1)[1]
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
        pending_suggestions[callback.from_user.id] = callback.message.chat.id if callback.message else 0
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


@dp.message(Command(commands=["start", "help"], ignore_case=True))
async def handle_start_help(message: Message):
    if not get_chat_setting(message.chat.id, 'welcome_enabled', True):
        return
    await message.answer(
        "Привет! Я — бот-агент. Упомяни меня в сообщении и задай вопрос,\n"
        "например: @bot_username Как мне сделать X?\n\n"
        "Если хочешь внести идею или предложку — нажми кнопку ниже.\n"
        "А ещё у нас есть крутое приложение с видео и обновлениями ✨",
        reply_markup=build_welcome_markup(message.chat.id),
    )


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

    source_texts = await collect_reply_context(replied, count + 3)
    if not source_texts:
        await message.reply("Не нашёл ни одного текстового сообщения для объединения.")
        return

    relevant_messages = select_relevant_messages(source_texts, max_items=count)
    if not relevant_messages:
        relevant_messages = source_texts[:count]

    merged_text = " \n".join(relevant_messages)
    resolved_style = get_quote_style(explicit_style) if explicit_style else infer_quote_style(merged_text, text)
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    reply_text = await generate_quote_reply(merged_text, resolved_style, text)
    await send_quote_with_feedback(
        message.chat.id,
        message.message_id,
        reply_text,
        source_chat_id=replied.chat.id,
        source_message_id=replied.message_id,
        source_user_id=getattr(replied.from_user, "id", None),
        source_username=getattr(replied.from_user, "username", None) or getattr(replied.from_user, "first_name", None),
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
        source = html.escape(format_quote_source(item))
        lines.append(
            f"{idx + 1}. <b>{text}</b> — 👍 {likes} 👎 {dislikes}\nИсточник: {source}"
        )

    await message.reply("Топ 5 оценённых цитат:\n" + "\n\n".join(lines), parse_mode="HTML")


@dp.message(Command(commands=["sticker", "savequote"], ignore_case=True))
async def handle_save_quote_sticker(message: Message):
    if not message.reply_to_message:
        await message.reply("Стикер можно сохранить только для AI-цитаты. Ответьте на сообщение с цитатой бота.")
        return

    replied = message.reply_to_message
    if not is_ai_quote_message(replied):
        await message.reply("Стикер можно сохранить только для AI-цитаты. Ответьте на сообщение с цитатой бота.")
        return

    text = (replied.text or replied.caption or "").strip()
    if not text:
        await message.reply("Не удалось извлечь текст AI-цитаты из ответа.")
        return

    quote_key = get_quote_key(text)
    quote_entry = quote_stats.get(quote_key) or ensure_quote_stats_entry(text)
    source_user_id = quote_entry.get("source_user_id")
    source_username = quote_entry.get("source_username")

    try:
        avatar_bytes = await get_user_avatar_bytes(int(source_user_id) if source_user_id is not None else None)
        sticker_bytes = await create_sticker_bytes(
            text,
            user_photo=avatar_bytes,
            username=source_username,
        )
        await bot.send_sticker(chat_id=message.chat.id, sticker=BufferedInputFile(sticker_bytes, filename="quote.webp"))
    except Exception as exc:
        print(f"Ошибка создания стикера: {exc}")
        traceback.print_exc()
        await message.reply("Не удалось создать стикер. Проверь, установлен ли Pillow.")


@dp.message()
async def handle_general_templates(message: Message):
    text = (message.text or message.caption or "").strip()
    if not text:
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
            "text": message.text or message.caption or "",
            "user_id": message.from_user.id,
            "chat_id": chat_id,
            "anonymous": anonymous,
        }
        await send_suggestion_to_admin(message, chat_id if chat_id else message.chat.id, suggestion_id, anonymous)
        await message.reply("Предложка отправлена админам.")
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

        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        status = await message.reply("🤖 Думаю...")
        try:
            ai_resp = await generate_ai_reply(clean_text)
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

    if get_chat_setting(message.chat.id, 'auto_quote_enabled', True) and random.random() < 0.08:
        try:
            await bot.send_chat_action(chat_id=message.chat.id, action="typing")
            reply_text = await generate_quote_reply(text, None, text)
            await send_quote_with_feedback(
                message.chat.id,
                None,
                reply_text,
                source_chat_id=message.chat.id,
                source_message_id=message.message_id,
                source_user_id=getattr(message.from_user, "id", None),
                source_username=getattr(message.from_user, "username", None) or getattr(message.from_user, "first_name", None),
            )
        except Exception as e:
            print(f"Ошибка периодического reply: {e}")
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