import hashlib
import html
import random
import re
import traceback
from typing import Any

from aiogram.types import Message

import bot_config
from bot_config import ai_client, bot, MODEL_CHAT, quote_stats
from helpers import get_chat_setting
from storage import get_recent_chat_context, save_quote_stats
from ai_utils import extract_text_source, is_openrouter_access_denied_error

FORBIDDEN_TERMS_RE = re.compile(
    r"\b(?:фарм(?:а|ы|ить|ить|ов|овка|инг|ингом)?|гринд(?:ить|ы|а|инг)?|farm(?:ing)?|grind(?:ing)?)\b",
    re.IGNORECASE,
)


def build_quote_system_prompt(style: str | None = None) -> str:
    resolved_style = get_quote_style(style)
    style_descriptions = {
        "genz": (
            "Ты — зумерский автор reply-цитат. Пиши живо, дерзко и с вайбом, как будто ты в чате с корешами. "
            "Используй современный русский сленг, но без пафоса и без английских слов."
        ),
        "roast": (
            "Ты — токсичный и дерзкий автор роаста. Пиши едко, с подколом, с зубастым сарказмом и холодной иронией. "
            "Не морализируй, не распинайся и не делай ответы слишком длинными."
        ),
        "toxic": (
            "Ты — саркастичный и максимально зубастый критик. Слова режут, но остаются точными и короткими. "
            "Используй злой, язвительный и дерзкий сленг без лишней воды."
        ),
    }
    description = style_descriptions.get(resolved_style, style_descriptions["toxic"])
    return (
        f"{description} "
        "Обязательно держи тон в одном из трёх режимов: зумерский, токсичный или зубастый. "
        "Никогда не упоминай фарму, гринд, farming, farm, grind и похожее. "
        "Если тема кажется близкой к этому, замени на ироничный или нейтральный оборот. "
        "Пиши 1–2 короткие фразы, без объяснений и без кавычек."
    )


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


def sanitize_quote_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""

    cleaned = FORBIDDEN_TERMS_RE.sub("", cleaned)
    cleaned = cleaned.strip(" \"'“”‘’")
    cleaned = re.sub(r"\b([А-Яа-яЁё]+)(?:\s+\1\b)+", r"\1", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,;:]){2,}", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)

    tokens = cleaned.split()
    if tokens:
        last_token = tokens[-1].strip(".,!?;:…")
        trailing_words = {"и", "а", "но", "или", "да", "чтобы", "когда", "если", "ли", "же", "то", "тут", "вот", "ну", "мда", "бро", "чел"}
        if last_token.lower() in trailing_words:
            tokens = tokens[:-1]
            cleaned = " ".join(tokens).strip()

    cleaned = cleaned.strip(" ,.;:!?\"")
    if not cleaned:
        return ""

    if not re.search(r"[.!?…]$", cleaned):
        cleaned = f"{cleaned}."

    return cleaned.strip()


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
            "Серьёзно? Это звучит так, будто твой вайб на нуле, а ты всё ещё делаешь вид, что это план, бро 😭",
            "Ты это сказал с таким лицом, будто у тебя зарядка на нуле, но ты всё ещё в режиме 'ну окей' и это уже смешно 🫠",
            "Это не аргумент, это просто очень сильный 'я в процессе' вайб, и всё, брат ✨",
            "Ты пишешь так, будто весь твой мир в трёх вкладках, а одна из них — паника, другая — мемы 😏",
            "Бро, звучит так, будто ты вышел из комнаты и всё ещё пытаешься быть в теме, но уже поздно 😂",
            "Ой, прям как будто ты тут главный герой, но только в своей голове, чел 😂",
            "Ну да, очень по-нашему: сначала шум, потом ноль смысла, и вот уже ты в роли легенды 😏",
        ]
    elif chosen_style == "roast":
        templates = [
            "Ты это написал с такой уверенностью, будто у тебя есть план на жизнь. Но нет, тут только шум и попытка выглядеть умным 💀",
            "С таким подходом ты бы даже с тенью спорил и всё равно проиграл, мда 😂",
            "Это не мысль, это просто воздух с претензией на смысл, и это уже смешно 😏",
            "Ты не споришь — ты просто красиво шумишь, будто у тебя есть аргумент, но его нет 🙃",
            "Вот это прям очень смелый текст, если не считать, что он пришёл из пустоты, по факту 🫠",
            "С таким вайбом ты бы даже в зеркало посмотрел и всё равно нашёл повод для драматургии 😂",
            "Ну, это было очень смело, если не считать, что ты просто снова выдал пустой шум 😏",
        ]
    else:
        templates = [
            "С таким текстом ты не споришь — ты просто шумно исчезаешь из разговора, будто это твой главный талант 😐",
            "Мда, это звучит как попытка выглядеть важным, но у тебя в запасе только пафос и пустота 😏",
            "Ты будто решил, что сарказм — это твой суперспособ, но на деле это просто фон с претензией 💀",
            "Вот это да, прям как будто ты вышел из чата и всё ещё хочешь быть главным героем, но никто не купился 😬",
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
    normalized_bot_username = safe_username(bot_username or bot_config.BOT_USERNAME)

    if normalized_chat_username:
        url = f"https://t.me/{normalized_chat_username}/{message_id}"
        label = f"t.me/{normalized_chat_username}/{message_id}"
        return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'

    if chat_id > 0:
        if normalized_bot_username:
            bot_url = f"https://t.me/{normalized_bot_username}"
            label = f"Личный диалог с @{normalized_bot_username}"
            return f'<a href="{html.escape(bot_url, quote=True)}">{html.escape(label)}</a> · <code>сообщение №{message_id}</code>'
        return f"<code>Личное сообщение №{message_id}</code>"

    if chat_id < 0:
        raw = str(abs(chat_id))
        clean = raw[3:] if raw.startswith("100") else raw
        url = f"https://t.me/c/{clean}/{message_id}"
        label = f"Чат #{clean} · сообщение #{message_id}"
        return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'

    return f"<code>Источник №{message_id}</code>"


def build_feedback_markup(text: str) -> Any:
    quote_key = get_quote_key(text)
    entry = quote_stats.get(quote_key) or ensure_quote_stats_entry(text)
    likes = int(entry.get("likes", 0))
    dislikes = int(entry.get("dislikes", 0))
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
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
    system_prompt = build_quote_system_prompt(resolved_style)
    system_prompt = f"{system_prompt} Никогда не обрывай слова или предложения; всегда завершай мысль полностью и дописывай окончания слов."
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
        "- Не повторяй слова подряд и не обрывай мысль на полуслове — всегда заканчивай фразу нормально.\n"
        "- Никогда не обрывай слова или предложения — всегда дочитывай и дописывай окончания слов, чтобы фразы были полными.\n"
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
            max_tokens=90,
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
            cleaned = sanitize_quote_text(reply_text)
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
