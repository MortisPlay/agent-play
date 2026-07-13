import hashlib
import html
import random
import re

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
    bot,
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
)
from helpers import (
    get_chat_setting,
    get_message_content,
    get_private_chat_template,
    get_suggestion_content,
    is_admin_user,
    is_private_chat,
    is_video_update_request,
)
from interactions import send_bug_report_to_admin, send_question_to_admin, send_suggestion_to_admin
from markup import build_admin_markup, build_welcome_markup
from quote_utils import (
    collect_reply_context,
    format_quote_source,
    get_quote_style,
    generate_quote_reply,
    infer_quote_style,
    is_ai_quote_message,
    select_relevant_messages,
    send_quote_with_feedback,
)
from storage import load_chat_settings, load_quote_stats, save_message_to_history


load_quote_stats()
load_chat_settings()


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
    if is_private_chat(message):
        await message.reply("Команда /top доступна только в группах.")
        return
    
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
        source = format_quote_source(item, bot_config.BOT_USERNAME)
        lines.append(
            f"{idx + 1}. <b>{text}</b> — 👍 {likes} 👎 {dislikes}\nИсточник: {source}"
        )

    await message.reply("Топ 5 оценённых цитат:\n" + "\n\n".join(lines), parse_mode="HTML")


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
