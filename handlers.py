import asyncio
import hashlib
import html
import random
import re
import time
import traceback

from aiogram.filters import Command
from aiogram.types import Message

from ai_utils import (
    describe_photo_bytes,
    download_file_bytes,
    extract_text_source,
    generate_ai_reply,
    guess_audio_format,
    transcribe_audio_bytes,
)
import bot_config
from bot_config import (
    ADMIN_IDS,
    BOT_USERNAME,
    bot,
    bug_report_requests,
    dp,
    pending_admin_comments,
    pending_bug_report_clarifications,
    pending_bug_reports,
    pending_questions,
    pending_suggestions,
    question_reply_targets,
    suggestion_anonymity,
    suggestion_requests,
    quote_stats,
    increment_stat,
)
from helpers import (
    _build_kira_reply,
    get_chat_setting,
    get_message_content,
    get_private_chat_template,
    get_suggestion_content,
    is_admin_user,
    is_kira_related_text,
    is_meme_template_text,
    is_mortis_intro_question,
    is_mortis_related_text,
    is_private_chat,
    is_video_update_request,
)
from interactions import send_bug_report_to_admin, send_question_to_admin, send_suggestion_to_admin
from markup import build_admin_markup, build_welcome_markup
from quote_utils import (
    format_quote_source,
    get_quote_style,
    generate_quote_reply,
    infer_quote_style,
    is_ai_quote_message,
    parse_quote_command_args,
    send_quote_with_feedback,
)
from storage import load_chat_settings, load_quote_stats, save_message_to_history


load_quote_stats()
load_chat_settings()


THINKING_STATUSES = [
    "🤖 Думаю...",
    "🤖 Анализирую...",
    "🤖 Обрабатываю...",
    "🤖 Ищу ответ...",
    "🤖 Включаю мозги...",
    "⏳ Сейчас...",
    "⏳ Момент...",
]


async def animate_thinking_status(message: Message, status_msg, duration: float = 30.0):
    """
    Анимирует статусное сообщение с разными текстами пока проходит обработка.
    duration — максимальное время обновления (сек)
    """
    if not status_msg:
        return
    
    try:
        start_time = asyncio.get_event_loop().time()
        index = 0
        last_text = ""
        while asyncio.get_event_loop().time() - start_time < duration:
            try:
                text = THINKING_STATUSES[index % len(THINKING_STATUSES)]
                # Только обновляем если текст действительно изменился
                if text != last_text:
                    await status_msg.edit_text(text)
                    last_text = text
                index += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Игнорируем ошибки "message not modified"
                if "not modified" not in str(e).lower():
                    print(f"Ошибка обновления статуса: {e}")
            await asyncio.sleep(0.8)
    except asyncio.CancelledError:
        pass


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
    await message.answer(
        "Привет! Я — бот-агент. Упомяни меня в сообщении и задай вопрос,\n"
        "например: @agentplay_bot Как мне сделать X?\n\n"
        "Если хочешь внести идею или предложку — нажми кнопку ниже.\n"
        "А ещё у нас есть крутое приложение с видео и обновлениями",
        reply_markup=build_welcome_markup(message.chat.id, include_private_only=is_private_chat(message)),
    )


@dp.message(Command(commands=["help"], ignore_case=True))
async def handle_help(message: Message):
    await message.answer(
        "Как пользоваться ботом:\n\n"
        "• Упомяни меня в чате и задай вопрос — я отвечу как агент.\n"
        "• Ответь на сообщение командой /q — я сделаю из него цитату.\n"
        "• Нажми кнопку ниже, если хочешь отправить предложку.",
        reply_markup=build_welcome_markup(message.chat.id, include_private_only=is_private_chat(message)),
    )


def _has_bot_mention(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    if BOT_USERNAME:
        return f"@{BOT_USERNAME.lower()}" in lowered
    return bool(re.search(r"@\w+", lowered))


@dp.message(Command(commands=["q"], ignore_case=True))
async def handle_quote_command(message: Message):
    increment_stat("commands_used")
    text = (message.text or "").strip()
    command_body = ""
    if text.lower().startswith("/q"):
        payload = text[2:].strip()
        if payload.startswith("@"):
            payload = payload.split(None, 1)[1] if " " in payload else ""
        command_body = payload.strip()
    command_tokens = command_body.split() if command_body else []
    explicit_style = parse_quote_command_args(command_tokens)

    replied = message.reply_to_message
    source_text = ""

    if command_body:
        source_text = command_body
    elif replied:
        # --- ГОЛОСОВЫЕ / АУДИО: транскрибация ---
        if replied.voice or replied.audio:
            file_id = getattr(replied.voice, "file_id", None) or getattr(replied.audio, "file_id", None)
            if file_id:
                audio_bytes = await download_file_bytes(file_id)
                if audio_bytes:
                    audio_format = guess_audio_format(getattr(replied.voice, "mime_type", None) or getattr(replied.audio, "mime_type", None))
                    source_text = await transcribe_audio_bytes(audio_bytes, audio_format)
            if not source_text.strip():
                await message.reply("Не удалось распознать голосовое сообщение. Попробуй ещё раз или отправь текст.")
                return
        # --- ФОТО: описание через AI vision + caption (если есть) ---
        elif replied.photo:
            photo = max(replied.photo, key=lambda item: (getattr(item, "width", 0) or 0) * (getattr(item, "height", 0) or 0))
            caption = (replied.caption or "").strip()
            if photo:
                photo_bytes = await download_file_bytes(photo.file_id)
                if photo_bytes:
                    description = await describe_photo_bytes(photo_bytes)
                    if caption and description:
                        source_text = f"{caption}\n\n{description}"
                    elif description:
                        source_text = description
                    elif caption:
                        source_text = caption
            if not source_text.strip():
                await message.reply("Не удалось описать фото. Попробуй ещё раз или отправь текст.")
                return
        # --- ВИДЕО: caption + описание через AI vision (thumbnail) ---
        elif replied.video or replied.video_note:
            caption = (replied.caption or "").strip()
            # Пробуем получить thumbnail видео (первый кадр) для описания
            video_description = ""
            thumbnail = getattr(replied.video, "thumbnail", None) if replied.video else None
            if thumbnail:
                thumb_bytes = await download_file_bytes(thumbnail.file_id)
                if thumb_bytes:
                    video_description = await describe_photo_bytes(thumb_bytes)
            if caption and video_description:
                source_text = f"{caption}\n\n{video_description}"
            elif video_description:
                source_text = video_description
            elif caption:
                source_text = caption
            if not source_text.strip():
                await message.reply("Не удалось описать видео. Попробуй ещё раз или отправь текст.")
                return
        # --- ТЕКСТ / ВСЁ ОСТАЛЬНОЕ ---
        else:
            source_text = await extract_text_source(replied)
            if not source_text.strip():
                await message.reply("Не нашёл текста в этом сообщении для цитаты.")
                return
    else:
        await message.reply("Ответь на сообщение или напиши текст после /q, чтобы я сделал из него цитату.")
        return

    if not source_text.strip():
        await message.reply("Не нашёл подходящего текста для цитаты.")
        return

    # Если ответили на AI-цитату — используем AI-генерацию с учётом контекста
    if replied and is_ai_quote_message(replied):
        # Берём текст AI-цитаты как контекст для новой генерации
        ai_quote_text = await extract_text_source(replied)
        if ai_quote_text.strip():
            source_text = f"В ответ на: {ai_quote_text}\n\n{source_text}"

    resolved_style = get_quote_style(explicit_style) if explicit_style else infer_quote_style(source_text, text)
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except Exception as exc:
        print(f"Ошибка отправки typing action: {exc}")

    reply_text = await generate_quote_reply(source_text, resolved_style, text, chat_id=message.chat.id)
    if replied is not None:
        await send_quote_with_feedback(
            message.chat.id,
            replied.message_id,
            reply_text,
            source_chat_id=replied.chat.id,
            source_message_id=replied.message_id,
            source_user_id=getattr(replied.from_user, "id", None),
            source_username=getattr(replied.from_user, "username", None) or getattr(replied.from_user, "first_name", None),
            source_chat_username=getattr(replied.chat, "username", None),
        )
    else:
        await send_quote_with_feedback(
            message.chat.id,
            message.message_id,
            reply_text,
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
            source_user_id=getattr(getattr(message, "from_user", None), "id", None),
            source_username=getattr(getattr(message, "from_user", None), "username", None) or getattr(getattr(message, "from_user", None), "first_name", None),
            source_chat_username=getattr(message.chat, "username", None),
        )


@dp.message(Command(commands=["topquotes", "top"], ignore_case=True))
async def handle_top_quotes(message: Message):
    increment_stat("commands_used")
    if is_private_chat(message):
        await message.reply("Команда /top доступна только в группах.")
        return
    
    chat_id = message.chat.id
    
    rated_quotes = [
        item
        for item in quote_stats.values()
        if (
            int(item.get("likes", 0)) > 0 or int(item.get("dislikes", 0)) > 0
        ) and (
            item.get("quote_chat_id") == chat_id or item.get("source_chat_id") == chat_id
        )
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
        await message.reply("Пока никто не оценивал цитаты в этом чате. Лайкай и собирай топ!")
        return

    lines = []
    for idx, item in enumerate(ranked):
        text = html.escape(str(item.get("text", "—")))
        likes = int(item.get("likes", 0))
        dislikes = int(item.get("dislikes", 0))
        
        # Ссылка на сообщение с цитатой
        quote_chat_id = item.get("quote_chat_id")
        quote_message_id = item.get("quote_message_id")
        if quote_chat_id and quote_message_id:
            try:
                clean_id = str(abs(int(quote_chat_id)))
                if clean_id.startswith("100"):
                    clean_id = clean_id[3:]
                quote_url = f"https://t.me/c/{clean_id}/{int(quote_message_id)}"
                link = f'<a href="{html.escape(quote_url, quote=True)}">🔗</a>'
            except (TypeError, ValueError):
                link = ""
        else:
            link = ""
        
        source = format_quote_source(item, bot_config.BOT_USERNAME)
        lines.append(
            f"{idx + 1}. {link} <b>{text}</b> — 👍 {likes} 👎 {dislikes}\nИсточник: {source}"
        )

    await message.reply("🏆 Топ 5 оценённых цитат в этом чате:\n" + "\n\n".join(lines), parse_mode="HTML")


def _build_mortis_chat_reply(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())

    # Оправдания после катки
    if any(keyword in normalized for keyword in ["пинг", "лаг", "лаги", "тимм", "тиммейт", "тиммейтов", "катка", "проиграл", "проигрыш", "из-за пинга", "из-за лагов"]):
        return random.choice([
            "После катки у всех бывают оправдания: пинг, лаги, плохие тиммейты... Но скилл либо есть, либо его нет 🎮",
            "Опять пинг и микрофризы помешали? Классика! Настоящий тащер вытягивает даже на 200ms.",
            "Да-да, конечно, девайсы подвели и тиммейты без монитора играли. Главное — верить!",
            "Проиграл не из-за лагов, а потому что ауры не хватило. В следующий раз заходи с настроем на победу 🤙",
            "Слышали уже про «пинг 999» и «мышка даблкликнула». Давай без отмазок, просто не затащил!",
            "Классика жанра: проиграл — виноват провайдер, выиграл — чистый скилл. Признай поражение достойно 😉",
            "Тиммейты без микрофона, FPS просел, софиты слепили... Что ещё помешало оформить победу?",
            "Пинг — это отговорка для тех, кому не хватило аима и тайминга. Тренируйся!",
        ])

    # Вызов на дуэль / 1 на 1
    if any(keyword in normalized for keyword in ["1 на 1", "1на1", "дуэль", "pvp", "вызов", "пошли", "пошли 1"]):
        return random.choice([
            "Заходи в лобби, раз на раз покажешь свой скилл или только в чате такой смелый?",
            "Если зовёшь на дуэль — давай без лишних слов. Один на один, честно, без отмазок!",
            "Погнали 1v1! Посмотрим, кто тут реально тащит, а кто сразу после первого спавна ливнёт 🎯",
            "Дуэль? Без проблем. Только потом не пиши, что у тебя мышка лагала!",
            "Слова в чате — это легко. Пошли в лобби, устроим проверку на прочность!",
            "Принимаю вызов! Заходи на карту, разберёмся по-мужски и без админок.",
            "1 на 1? Надеюсь, ты подготовился, потому что уступок и скидок не будет!",
        ])

    # Сравнение с другими стримерами / Киберспорт
    if any(keyword in normalized for keyword in ["сравни", "сравнение", "стример", "стримеры", "киберспорт", "кибер", "конкурен", "вне конкуренции", "лучше всех"]):
        return random.choice([
            "Сравнивать Мортиса с другими стримерами — это как спорить с погодой. В киберспорте он вне конкуренции по вайбу и харизме!",
            "Зачем сравнивать? Пока другие разгоняют драму, Мортис делает контент и копит ауру ⚡",
            "В киберспорте важен не только аим, но и атмосфера. А по атмосфере Мортис уже давно на вершине.",
            "Стримеров много, а Мортис один. Сравнения тут просто бессмысленны!",
            "У каждого свой стиль, но Мортис берет харизмой и подачей, которые не скопируешь.",
            "Конкуренты пусть стараются дальше, а мы тут просто делаем качество и держим стиль 👑",
            "Зачем смотреть на других, если можно смотреть на лучшее? Вопрос риторический.",
        ])

    # Шутки / По приколу
    if any(keyword in normalized for keyword in ["шут", "шуточ", "шутка", "смешно", "смешно?", "смешно)", "типо", "как будто", "по приколу", "просто шутка", "это шутка", "это joke", "joke"]):
        return random.choice([
            "За шутку оценил, юмор засчитан 😉",
            "Ну если это по приколу, то ладно. Главное, чтобы без негатива!",
            "Оценил рофл, подкол засчитан 👍",
            "Шутка нормальная, двигаемся дальше!",
            "Рофлы принимаются, главное держим позитивный вайб!",
            "Ха-ха, посмеялись и хватит. Возвращаемся к делу!",
            "Прикол забавный, на 7 из 10 потянет 😄",
        ])

    # Запреты / Блокировки / РКН
    if any(keyword in normalized for keyword in ["заблокир", "заблокировать", "блок", "ркн", "запрет"]):
        return random.choice([
            "Если бы за такие вопросы блокировали, тут половина чата уже отдыхала бы 😑",
            "Запреты — это не к нам. Мы тут за чистый вайб и адекватное общение.",
            "Блокировать никого не будем, но задуть адекватности в чат точно не помешает!",
            "Бан-молоточек всегда напоготове, но дадим шансу адекватному диалогу 🔨",
            "Главное правило: без жесткого оффтопа и оскорблений, и никакой блокировки не будет.",
        ])

    # Оскорбления / Грубость
    if any(keyword in normalized for keyword in ["сосал", "сосать", "сосёт", "сосет", "сосёшь", "сосешь"]):
        return random.choice([
            "Откуда тебе знать? Я — бот-агент. А вот сливать катки и искать виноватых — это уже твоя фишка 👾",
            "Меньше токсичности, больше побед в катках. Направь эту энергию в игру!",
            "Слабый байт. Попробуй выдать что-то поумнее.",
            "Токсичность скилла не добавляет. Попробуй тренировать аим, а не чат!",
            "Детский сад какой-то. Давай без байтов и по делу.",
        ])

    # Оскорбления / Лох / Дебил
    if any(keyword in normalized for keyword in ["лох", "дебил", "тупой", "туп", "мразь", "мраз"]):
        return random.choice([
            "Вместо того чтобы кидаться словами, лучше бы скилл прокачал.",
            "Тебе не дадут доказать, что кто-то в этом чате такой. Думай сам.",
            "Агро-режим активирован? Расслабься, здесь все свои.",
            "Крутой в чате — слабый в игре? Не надо так, держи себя в руках.",
            "Словесный поток не засчитан. Попробуй аргументировать нормальным языком.",
        ])

    # Цитаты / Придумай
    if any(keyword in normalized for keyword in ["цитат", "придумай", "цитируй", "напиши"]):
        return random.choice([
            "Уже придумал: «Мортис — настоящий сигма: копит ауру и доказывает всё делами на стриме» 🤙",
            "Вот тебе база: «Главное в катке — не идеальный пинг, а железобетонная уверенность в себе».",
            "Лови: «Скилл приходит и уходит, а харизма остаётся навсегда».",
            "Мудрость дня: «Не бойся проиграть катку, бойся потерять уверенность в своем аиме».",
            "Лови мысль: «Настоящий киберспортсмен молча забирает раунд, пока остальные спорят в чате».",
        ])

    # По фактам / Серьёзно
    if any(keyword in normalized for keyword in ["серьёзно", "серьезно", "по фактам", "реально", "спокойно"]):
        return random.choice([
            "Если по фактам: я защищаю стримера и разработчика спокойно и аргументированно. Без лишних эмоций.",
            "По делу так по делу: цифры и факты решают, всё остальное — просто шум.",
            "Без драмы и суеты: работаем на результат, а на негатив не отвлекаемся.",
            "Разговор по фактам — это наш профиль. Разложим всё по полочкам и без эмоций.",
            "Если подходить серьезно, то результаты говорят сами за себя. Остальное — пустые разговоры.",
        ])

    # Игра слов с 3.14
    if any(keyword in normalized for keyword in ["3.14", "314", "рас", "3 14", "3,14", "314рас", "3.14рас", "3,14рас"]):
        if any(keyword in normalized for keyword in ["реально", "серьёзно", "серьезно", "на самом деле", "по делу", "задева", "обид", "оскорб"]):
            return "Если это попытка задеть — мимо. Вижу только пустое место вместо аргументов."
        return random.choice([
            "Математический рофл засчитан, 3.14159... 🤓",
            "О, шутки из курса геометрии подъехали! Понимаю)",
            "Число Пи вспомнили? Алгебра на уровне, респект 📐",
        ])

    # Дефолтный ответ
    return random.choice([
        "Если речь про Мортиса, то я всегда отвечу спокойно, по фактам и без лишнего хамства.",
        "Я здесь, чтобы поддерживать порядок и правильный вайб. Задавай нормальные вопросы!",
        "Всё под контролем. Главное — соблюдать уважение и играть в удовольствие 🤙",
        "На связи! Задавай вопрос или просто не мешай делать качественный контент.",
        "Пока ты пишешь — Мортис копит ауру. Общаемся вежливо и по делу!",
    ])


async def _reply_to_agent_message(message: Message, clean_text: str, context_text: str | None = None) -> None:
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    except Exception as exc:
        print(f"Ошибка отправки typing action: {exc}")

    status = await message.reply("🤖 Думаю...")
    animation_task = asyncio.create_task(animate_thinking_status(message, status, duration=35.0))

    try:
        ai_resp = await generate_ai_reply(clean_text, context_text=context_text)
        animation_task.cancel()
        try:
            await animation_task
        except asyncio.CancelledError:
            pass
        try:
            await status.edit_text(ai_resp)
        except Exception as e:
            print(f"Ошибка редактирования статуса: {e}")
            await message.reply(ai_resp)
    except Exception as e:
        print(f"Ошибка при ответе ИИ: {e}")
        traceback.print_exc()
        animation_task.cancel()
        try:
            await animation_task
        except asyncio.CancelledError:
            pass
        try:
            await status.edit_text("Ошибка при получении ответа от ИИ.")
        except Exception as e:
            print(f"Ошибка редактирования ошибки: {e}")
            await message.reply("Ошибка при получении ответа от ИИ.")


def _should_answer_without_mention(message: Message, text: str) -> bool:
    if not is_private_chat(message):
        return False

    lowered = (text or "").strip().lower()
    if not lowered:
        return False

    mortis_keywords = ["мортис", "mortis", "mortisplay", "мортиса", "мортиса"]
    return any(keyword in lowered for keyword in mortis_keywords)


@dp.message()
async def handle_general_templates(message: Message):
    text = get_message_content(message).strip()
    has_media = any([message.photo, message.video, message.video_note, message.voice, message.audio, message.document])
    if message.from_user and not message.from_user.is_bot and message.text and not message.text.startswith("/"):
        save_message_to_history(message.chat.id, message.text)
    if not text and not has_media:
        return

    if message.text and message.text.startswith("/") and not message.text.lower().startswith("/q"):
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

    if message.from_user and message.from_user.id in pending_bug_reports:
        pending_bug_reports.pop(message.from_user.id, None)
        report_id = hashlib.sha256(f"{message.from_user.id}:{time.time()}".encode("utf-8")).hexdigest()[:10]
        bug_report_requests[report_id] = {
            "text": get_suggestion_content(message),
            "user_id": message.from_user.id,
            "chat_id": message.chat.id,
        }
        if not message.photo and not message.video and not message.voice and not message.audio and not message.document and not get_suggestion_content(message):
            await message.reply("📸 Пришлите скриншот и кратко опишите баг, чтобы отправить отчёт.")
            return
        await send_bug_report_to_admin(message, message.chat.id, report_id)
        await message.reply(
            "Спасибо, что помогаете развивать сообщество и находите ошибки."
            " Мы передали ваш отчёт администратору."
        )
        return

    if message.from_user and message.from_user.id in pending_bug_report_clarifications:
        clarification_text = get_message_content(message).strip()
        report_id = pending_bug_report_clarifications.pop(message.from_user.id)
        entry = bug_report_requests.get(report_id)
        if entry and entry.get("user_id"):
            try:
                await bot.send_message(
                    chat_id=int(entry["user_id"]),
                    text=f"💬 Уточнение от администратора:\n\n{clarification_text or 'Без текста'}",
                )
            except Exception:
                pass
            await message.reply("Уточнение отправлено пользователю.")
        else:
            await message.reply("Не удалось отправить уточнение.")
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

    if text.lower().strip() == "прохладно 🤣":
        await message.reply("ладно")
        return

    if is_meme_template_text(text):
        template = get_private_chat_template(text)
        if template:
            await message.reply(template)
            return

    if is_mortis_intro_question(text):
        template = get_private_chat_template(text)
        if template:
            await message.reply(template)
            return

    if is_mortis_related_text(text):
        if is_private_chat(message) or _has_bot_mention(text):
            await message.reply(_build_mortis_chat_reply(text))
            return

    if is_kira_related_text(text):
        if is_private_chat(message) or _has_bot_mention(text):
            await message.reply(_build_kira_reply(text))
            return

    if is_private_chat(message):
        template = get_private_chat_template(text)
        if template:
            await message.reply(template)
            return

        if _should_answer_without_mention(message, text):
            context_text = get_private_chat_template(text)
            await _reply_to_agent_message(message, clean_text=text, context_text=context_text)
            return

    global BOT_USERNAME
    if not BOT_USERNAME:
        try:
            me = await bot.get_me()
            BOT_USERNAME = me.username if me and me.username else None
        except Exception:
            BOT_USERNAME = None

    if get_chat_setting(message.chat.id, 'ai_enabled', True) and (
        (BOT_USERNAME and f"@{BOT_USERNAME.lower()}" in text.lower()) or _should_answer_without_mention(message, text)
    ):
        if BOT_USERNAME and f"@{BOT_USERNAME.lower()}" in text.lower():
            increment_stat("mentions")
            clean_text = re.sub(rf"@{re.escape(BOT_USERNAME)}", "", text, flags=re.I).strip()
        else:
            clean_text = text.strip()

        if not clean_text:
            await message.reply("Да? Чем помочь? Напишите вопрос после упоминания бота.")
            return

        context_text = get_private_chat_template(clean_text)
        await _reply_to_agent_message(message, clean_text=clean_text, context_text=context_text)
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
        get_chat_setting(message.chat.id, 'auto_quote_enabled', True) and random.random() < 0.03
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
