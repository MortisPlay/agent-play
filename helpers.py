import re
from typing import Any

from aiogram.types import Message

from bot_config import ADMIN_IDS, BOT_USERNAME, chat_settings
from storage import save_chat_settings


def is_admin_user(user: Any | None) -> bool:
    if not user:
        return False
    if not ADMIN_IDS:
        return True
    return int(getattr(user, "id", 0)) in ADMIN_IDS


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


def get_suggestion_content(message: Message) -> str:
    text = (getattr(message, "text", None) or "").strip()
    caption = (getattr(message, "caption", None) or "").strip()

    if text and caption and text != caption:
        return f"{text}\n\n{caption}"
    if text:
        return text
    if caption:
        return caption
    return ""


def get_message_content(message: Message) -> str:
    text = (getattr(message, "text", None) or "").strip()
    caption = (getattr(message, "caption", None) or "").strip()

    if text and caption and text != caption:
        return f"{text}\n\n{caption}"
    return text or caption or ""


def is_video_update_request(text: str) -> bool:
    text = text.lower()
    has_video = any(word in text for word in ["видео", "ролик", "вылож", "анонс"])
    has_time_question = any(word in text for word in ["когда", "скоро", "когда новое", "когда выйдет", "когда будет"])
    return has_video and has_time_question


def is_private_chat(message: Message) -> bool:
    chat = getattr(message, "chat", None)
    if chat is None:
        return False
    chat_type = getattr(chat, "type", None)
    if chat_type == "private":
        return True
    chat_id = getattr(chat, "id", None)
    return bool(chat_id and int(chat_id) > 0)


def get_private_chat_template(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not normalized:
        return None

    if any(phrase in normalized for phrase in ["кто такой mortisplay", "расскажи про mortisplay", "про ютуб", "кто такой ютубер", "кто такой mortis"]):
        return (
            "Mortisplay — это ютубер, который занимается записью видеоигр, выкладывает угары, баги и эпики, а также снимает ролики вместе с друзьями, особенно rol1j, Johnny_Drill и другими. "
            "Он уже больше 5 лет пытается добиться успеха, но мир не даёт ему покоя, чтобы он достиг своих 1К подписчиков и в какой-то момент нашёл свою славу. "
            "За кулисами он также снимает разные видеоролики, гайды, подкасты и анимации — раньше больше этим занимался, сейчас уже не так часто. "
            "У него есть 3 канала: Mortisplay32 (основной), Mortisplay_Studio (дополнительный) и F.U.J.I.N.mk56 (старый и заброшенный контент по SO2). "
            "Если тебе было интересно послушать про разработчика этого агента, то приходи на его Telegram-канал: https://t.me/MortisPlayTG\n\n"
            "Спасибо за внимание ❤️"
        )

    if any(phrase in normalized for phrase in ["что за сайт mortisplay.ru", "что за сайт", "про сайт", "про mortisplay.ru", "сайт mortisplay"]):
        return (
            "mortisplay.ru — это новый высокий уровень в карьере Mortis'a, где есть много развлекательного контента с ютуба и много крутых кнопочек. "
            "В основном Mortis создавал его для своей аудитории, но пользовались им не так часто, потому что тогда у него была ещё маленькая аудитория. "
            "Но уже сайту исполнился 1 год, и он стал частью семейной карьеры Mortis'a ❤️ "
            "На сайте есть разделы Видео, Twitch и другие разделы. Всё остальное можно посмотреть прямо на сайте mortisplay.ru.\n\n"
            "Спасибо за внимание ❤️"
        )

    if any(phrase in normalized for phrase in ["что за агент", "зачем создан агент", "для чего бот", "что это за агент", "что за бот"]):
        return (
            "Привет! Спасибо, что задал такой вопрос, но всё это лучше расскажет разработчик, потому что он в этом понимает больше: "
            "По сути, это обычный бот, но мы решили сделать его в агента, потому что он оснащён хорошим искусственным интеллектом. "
            "Через него можно делать ИИ-цитаты в группах, а также смотреть наш сайт прямо в Telegram через бота-агента. "
            "Он ещё может иногда работать неидеально, потому что это первый стабильный бот в Telegram, который разработчик когда-либо делал. "
            "В основном его делали для ИИ-цитат в чате, но в итоге он может стать самым развитым ботом-агентом."
        )

    if any(phrase in normalized for phrase in ["зачем он придумал ник mortis", "почему ник mortis", "почему mortis", "зачем придумал ник"]):
        return (
            "Хо хо хо, хороший вопрос, на котором сам Mortis затрудняется ответить. "
            "Но всё очень просто: он просто придумал этот ник в голове, сплагиатил его из собственного вдохновения и так появился на свет. "
            "При этом он ничего не украл, ни у Бравла, ни у кого-то другого."
        )

    return None
