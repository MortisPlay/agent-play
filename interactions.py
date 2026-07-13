import traceback

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot_config import ADMIN_IDS, bot, question_reply_targets
from helpers import get_suggestion_content


async def send_bug_report_to_admin(message: Message, chat_id: int, report_id: str) -> None:
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"bug_report_accept:{report_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"bug_report_decline:{report_id}"),
            ],
            [InlineKeyboardButton(text="💬 Уточнить", callback_data=f"bug_report_clarify:{report_id}")],
        ]
    )

    user = getattr(message, "from_user", None)
    full_name_parts = [part for part in [getattr(user, "first_name", None), getattr(user, "last_name", None)] if part]
    full_name = " ".join(full_name_parts) if full_name_parts else "Пользователь"
    username = getattr(user, "username", None)
    user_id = getattr(user, "id", None)

    lines: list[str] = ["👾 Новый отчёт об ошибке"]
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
        base_text = "👾 Новый отчёт об ошибке"

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
        print(f"Ошибка отправки отчёта об ошибке админам: {exc}")
        traceback.print_exc()


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
    full_name_parts = [part for part in [getattr(user, "first_name", None), getattr(user, "last_name", None)] if part]
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
    full_name_parts = [part for part in [getattr(user, "first_name", None), getattr(user, "last_name", None)] if part]
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
                from bot_config import question_reply_targets
                question_reply_targets[(int(recipient_id), int(sent_message.message_id))] = {
                    "user_id": int(user_id) if user_id is not None else None,
                    "chat_id": int(chat_id) if chat_id is not None else None,
                    "question_id": question_id,
                }
        except Exception as exc:
            print(f"Ошибка отправки вопроса админам: {exc}")
            traceback.print_exc()
