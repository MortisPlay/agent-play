import hashlib
import time
import traceback

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot_config import (
    ADMIN_IDS,
    bot,
    dp,
    bug_report_requests,
    pending_admin_comments,
    pending_bug_report_clarifications,
    pending_bug_reports,
    pending_questions,
    pending_suggestions,
    question_reply_targets,
    suggestion_anonymity,
    quote_stats,
    suggestion_requests,
)
from helpers import get_chat_setting, get_suggestion_content, is_admin_user, set_chat_setting
from markup import build_admin_markup
from quote_utils import ensure_quote_stats_entry, format_quote_source, build_feedback_markup, build_quote_display_text
from storage import save_quote_stats


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


@dp.callback_query(lambda callback: callback.data == "bug_report_open")
async def handle_bug_report_open(callback: CallbackQuery):
    if not callback.from_user:
        await callback.answer("Не удалось начать отправку отчёта.")
        return

    pending_bug_reports[callback.from_user.id] = callback.message.chat.id if callback.message else 0
    await callback.answer("Отправьте скриншот и кратко опишите баг.")
    try:
        await callback.message.answer(
            "📸 Пришлите скриншот и кратко напишите, что за ошибка или баг вы нашли."
            " После этого мы рассмотрим ваш отчёт."
        )
    except Exception:
        pass


@dp.callback_query(lambda callback: callback.data == "stars_open")
async def handle_stars_open(callback: CallbackQuery):
    await callback.answer("Пока что в разработке")
    try:
        await callback.message.answer("💫 Покупка звёзд пока что в разработке. Скоро добавим это здесь.")
    except Exception:
        pass


@dp.callback_query(lambda callback: callback.data and callback.data.startswith("bug_report_"))
async def handle_bug_report_callbacks(callback: CallbackQuery):
    data = callback.data or ""
    if data.startswith("bug_report_accept:"):
        report_id = data.split(":", 1)[1]
        entry = bug_report_requests.get(report_id)
        if entry:
            await callback.answer("Отчёт принят.")
            try:
                await callback.message.edit_text(f"✅ Принято\n\n{entry.get('text', '')}", reply_markup=None)
            except Exception:
                pass
            if entry.get("user_id"):
                try:
                    await bot.send_message(
                        chat_id=int(entry["user_id"]),
                        text="✅ Ваше уведомление об ошибке принято. Администратор взялся за работу.",
                    )
                except Exception:
                    pass
        return

    if data.startswith("bug_report_decline:"):
        report_id = data.split(":", 1)[1]
        entry = bug_report_requests.get(report_id)
        if entry:
            await callback.answer("Отчёт отклонён.")
            try:
                await callback.message.edit_text(f"❌ Отклонено\n\n{entry.get('text', '')}", reply_markup=None)
            except Exception:
                pass
            if entry.get("user_id"):
                try:
                    await bot.send_message(
                        chat_id=int(entry["user_id"]),
                        text="❌ Ваше уведомление об ошибке отклонено. Спасибо, что помогаете улучшать проект.",
                    )
                except Exception:
                    pass
        return

    if data.startswith("bug_report_clarify:"):
        report_id = data.split(":", 1)[1]
        pending_bug_report_clarifications[callback.from_user.id] = report_id
        await callback.answer("Напишите уточнение пользователю.")
        try:
            await callback.message.answer("💬 Напишите уточнение для пользователя, которое нужно отправить ему в ответ.")
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
                reply_markup=
                    InlineKeyboardMarkup(
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
