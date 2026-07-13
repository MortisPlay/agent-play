from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from helpers import get_chat_setting


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


def build_welcome_markup(chat_id: int | None = None, *, include_private_only: bool = False) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    if get_chat_setting(chat_id, 'app_button_enabled', True):
        buttons.append([InlineKeyboardButton(text="🚀 Открыть приложение", web_app=WebAppInfo(url="https://mortisplay.ru"))])
    if include_private_only:
        buttons.append([InlineKeyboardButton(text="👾 Сообщить об ошибке", callback_data="bug_report_open")])
    buttons.append([InlineKeyboardButton(text="❓ Есть вопрос!🤓", callback_data="question_open")])
    if get_chat_setting(chat_id, 'suggestion_button_enabled', True):
        buttons.append([InlineKeyboardButton(text="💡 Кинуть предложку", callback_data="suggestion_open")])
    if not buttons:
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть приложение", web_app=WebAppInfo(url="https://mortisplay.ru"))]])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
