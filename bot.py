import asyncio
import base64
import hashlib
import html
import io
import json
import os
import random
import re
import traceback
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
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

# Инициализация бота и клиента ИИ
bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()

# Подключаем AsyncOpenAI к серверу OpenRouter
ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENAI_API_KEY,
)

quote_stats: dict[str, dict[str, Any]] = {}

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - optional dependency for stickers
    Image = None
    ImageDraw = None
    ImageFont = None


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
        if any(marker in lowered for marker in ["http://", "https://", "@", "/q", "/start", "/help"]):
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


def ensure_quote_stats_entry(text: str) -> dict[str, Any]:
    quote_key = get_quote_key(text)
    entry = quote_stats.get(quote_key)
    if not entry:
        entry = {"text": text, "likes": 0, "dislikes": 0, "voters": {}}
        quote_stats[quote_key] = entry
    else:
        voters = entry.get("voters")
        if not isinstance(voters, dict):
            entry["voters"] = {}
    return entry


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


def create_sticker_bytes(text: str) -> bytes:
    if Image is None or ImageDraw is None or ImageFont is None:
        raise RuntimeError("Pillow не установлен")

    width, height = 512, 512
    image = Image.new("RGBA", (width, height), (18, 18, 18, 255))
    draw = ImageDraw.Draw(image)

    font_path = None
    if os.path.exists(FONT_PATH):
        font_path = FONT_PATH
    elif os.path.exists(str(DATA_DIR / FONT_PATH)):
        font_path = str(DATA_DIR / FONT_PATH)

    font = None
    if font_path:
        try:
            font = ImageFont.truetype(font_path, 34)
        except Exception:
            font = ImageFont.load_default()
    if font is None:
        font = ImageFont.load_default()

    max_width = width - 80
    words = text.split()
    lines: list[str] = []
    current_line = ""
    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] <= max_width or not current_line:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    if not lines:
        lines = [text[:40]]

    line_height = 48
    text_height = len(lines) * line_height
    start_y = max(40, (height - text_height) // 2)

    for index, line in enumerate(lines):
        y = start_y + index * line_height
        draw.text((40, y), line, fill=(255, 255, 255, 255), font=font)

    output = io.BytesIO()
    image.save(output, format="WEBP")
    return output.getvalue()


async def send_quote_with_feedback(chat_id: int, reply_to_message_id: int | None, reply_text: str) -> None:
    ensure_quote_stats_entry(reply_text)
    display_text = build_quote_display_text(reply_text)
    markup = build_feedback_markup(reply_text)
    if reply_to_message_id:
        await bot.send_message(chat_id=chat_id, text=display_text, reply_to_message_id=reply_to_message_id, reply_markup=markup)
    else:
        await bot.send_message(chat_id=chat_id, text=display_text, reply_markup=markup)


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


@dp.message(Command(commands=["start", "help"], ignore_case=True))
async def handle_start_help(message: Message):
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть приложение🤘", web_app=WebAppInfo(url="https://mortisplay.ru"))]
        ]
    )
    await message.answer(
        "Привет! Я — бот-агент. Упомяни меня в сообщении и задайте вопрос,\n"
        "например: @bot_username Как мне сделать X?\n"
        "А также в нашем агент-боте есть наш собственный сайт! Заходите и смотрите наши видеоролики ПРЯМО С НАШЕГО БОТА!",
        reply_markup=markup,
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
    ensure_quote_stats_entry(reply_text)
    await send_quote_with_feedback(message.chat.id, message.message_id, reply_text)


@dp.message(Command(commands=["topquotes", "top"], ignore_case=True))
async def handle_top_quotes(message: Message):
    ranked = sorted(
        quote_stats.values(),
        key=lambda item: (int(item.get("likes", 0)), -int(item.get("dislikes", 0))),
        reverse=True,
    )[:5]

    if not ranked:
        await message.reply("Пока никто не оценивал цитаты. Лайкай и собирай топ!")
        return

    lines = [
        f"{idx + 1}. <b>{html.escape(str(item.get('text', '—')))}</b> — 👍 {item.get('likes', 0)}"
        for idx, item in enumerate(ranked)
    ]
    await message.reply("Топ 5 залайканных цитат:\n" + "\n".join(lines), parse_mode="HTML")


@dp.message(Command(commands=["sticker", "savequote", "qs"], ignore_case=True))
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

    try:
        sticker_bytes = create_sticker_bytes(text)
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

    global BOT_USERNAME
    if not BOT_USERNAME:
        try:
            me = await bot.get_me()
            BOT_USERNAME = me.username if me and me.username else None
        except Exception:
            BOT_USERNAME = None

    if BOT_USERNAME and f"@{BOT_USERNAME.lower()}" in text.lower():
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
            "Бро, время летит как со скоростью ходьбы, следи за анонсом)",
            "Мортис многое мог обещать но доказать ему не дали - следи за анонсом на нашем канале)",
            "Я уже красный, культурно не получится!!! Не задавай глупые вопросы и жди - там всё распишут на нашем канале.",
        ]
        await message.reply(random.choice(answers))
        return

    if random.random() < 0.08:
        try:
            await bot.send_chat_action(chat_id=message.chat.id, action="typing")
            reply_text = await generate_quote_reply(text, None, text)
            ensure_quote_stats_entry(reply_text)
            await send_quote_with_feedback(message.chat.id, None, reply_text)
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