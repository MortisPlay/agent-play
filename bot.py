import asyncio
import base64
import io
import os
import random
import re
import traceback
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
import openai
from openai import AsyncOpenAI
import httpx

# Load env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FONT_PATH = os.getenv("FONT_PATH", "Roboto-Bold.ttf")
BOT_USERNAME: str | None = None

# Behavior tuning (probabilities)
# Поведение бота-агента (конфигурация через env при необходимости)

# Инициализация бота и клиента ИИ
bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()

# Подключаем AsyncOpenAI к серверу OpenRouter
ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENAI_API_KEY,
)

# Удалены вспомогательные функции генерации цитат и модулей для создания стикеров.


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
            model="meta-llama/llama-3-8b-instruct",
            messages=messages,
            max_tokens=200,
            temperature=0.7,
            top_p=0.95,
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


# Утилиты для нарезки текста и генерации изображений удалены — бот теперь агент без стикеров.


def is_video_update_request(text: str) -> bool:
    text = text.lower()
    has_video = any(word in text for word in ["видео", "ролик", "вылож", "анонс"])
    has_time_question = any(word in text for word in ["когда", "скоро", "когда новое", "когда выйдет"])
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


def select_relevant_messages(messages: list[str]) -> list[str]:
    if not messages:
        return []
    filtered = []
    for item in messages:
        text = re.sub(r"\s+", " ", item).strip()
        if not text:
            continue
        lowered = text.lower()
        if len(text) <= 3:
            continue
        if any(marker in lowered for marker in ["http://", "https://", "@", "/q", "/start", "/help"]):
            continue
        filtered.append(text)
    if filtered:
        return filtered
    return messages[:2]


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
            model="meta-llama/llama-3-8b-instruct",
            messages=[
                {"role": "system", "content": "Ты — талантливый автор саркастичных и стильных reply-ответов в Telegram."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=80,
            temperature=0.95,
            top_p=0.95,
        )
        if not getattr(response, "choices", None):
            return build_quote_reply(text, resolved_style)
        choice = response.choices[0]
        content = getattr(getattr(choice, "message", {}), "content", None)
        reply_text = content.strip() if content else ""
        if reply_text:
            return add_style_emoji(reply_text, resolved_style)
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


# --- ФУНКЦИЯ СОЗДАНИЯ КАРТИНКИ (QUOTLY-STYLE С БАБЛОМ) ---
# Функции, связанные с генерацией изображения/стикера, удалены — бот теперь агент.


# --- ХЕНДЛЕР КОМАНДЫ /start и /help ---
@dp.message(Command(commands=["start", "help"], ignore_case=True))
async def handle_start_help(message: Message):
    await message.answer(
        "Привет! Я — бот-агент. Упомяни меня в сообщении и задайте вопрос,\n"
        "например: @bot_username Как мне сделать X?\n" \
        "А также в нашем агент-боте есть наш собственный сайт! Заходите и смотрите наши видеоролики ПРЯМО С НАШЕГО БОТА! Чтобы зайти на сайт нажмите на кнопочку."
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

    source_texts = await collect_reply_context(replied, count + 2)
    if not source_texts:
        await message.reply("Не нашёл ни одного текстового сообщения для объединения.")
        return

    relevant_messages = select_relevant_messages(source_texts)[:count]
    if not relevant_messages:
        relevant_messages = source_texts[:count]

    merged_text = " \n".join(relevant_messages)
    resolved_style = get_quote_style(explicit_style) if explicit_style else infer_quote_style(merged_text, text)
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    reply_text = await generate_quote_reply(merged_text, resolved_style, text)
    await message.reply(reply_text)


@dp.message()
async def handle_general_templates(message: Message):
    text = (message.text or message.caption or "").strip()
    if not text:
        return

    if message.text and message.text.startswith("/"):
        return
    if message.from_user and message.from_user.is_bot:
        return
    if message.reply_to_message:
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
        await message.reply("Скоро - следи за каналом, там выкладываем всякие анонсы.")
        return

    if random.random() < 0.08:
        try:
            await bot.send_chat_action(chat_id=message.chat.id, action="typing")
            reply_text = await generate_quote_reply(text, None, text)
            await message.reply(reply_text)
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