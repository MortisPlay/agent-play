import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from openai import AsyncOpenAI

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
pending_bug_reports: dict[int, int] = {}
pending_bug_report_clarifications: dict[int, str] = {}
question_reply_targets: dict[tuple[int, int], dict[str, Any]] = {}
bug_report_requests: dict[str, dict[str, Any]] = {}

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


import time

dp.message.outer_middleware(AntiFloodMiddleware())

ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENAI_API_KEY,
)

quote_stats: dict[str, dict[str, Any]] = {}
suggestion_requests: dict[str, dict[str, Any]] = {}
