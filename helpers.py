import random
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


def is_meme_template_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not normalized:
        return False

    meme_phrases = [
        "67", "six seven", "шесть семь", "6 7", "6-7", "6/7", "sixseven", "s67", "шестьсемь", "шестьсем",
        "мем 67", "про 67", "про мем 67", "мем про 67",
        "я, робот", "я робот", "робот сочинит", "превратит в кусок холста", "шедевроисскуства", "робот превратит",
    ]
    return any(phrase in normalized for phrase in meme_phrases)


def is_mortis_related_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not normalized:
        return False

    mortis_keywords = [
        "мортис", "mortis", "mortisplay", "мортисplay",
        "мортиса", "mortis'a", "мортиса?", "mortis?",
        "мортиса!", "mortis!", "мортиса,", "mortis,",
        "мортис?", "mortis?", "мортис!", "mortis!"
    ]
    if any(keyword in normalized for keyword in mortis_keywords):
        return True

    return any(phrase in normalized for phrase in [
        "про мортиса", "про mortis", "про мортис",
        "про mortisplay", "про мортисplay",
        "цитату про мортиса", "цитату про mortis",
        "ты бы заблокировал мортиса", "ты бы заблокировал mortis",
        "мортис лох", "mortis лох",
        "мортис сосал", "mortis сосал",
        "мортис?", "mortis?"
    ])


def is_mortis_intro_question(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not normalized:
        return False

    return any(phrase in normalized for phrase in [
        "кто такой mortisplay",
        "кто такой мортисplay",
        "расскажи про mortisplay",
        "расскажи про мортисplay",
        "кто такой mortis",
        "кто такой мортис",
        "кто такой mortis?",
        "кто такой мортис?",
        "кто такой mortisplay?",
        "кто такой мортисplay?",
        "расскажи про mortis",
        "расскажи про мортиса",
        "расскажи про mortisplay",
        "расскажи про мортисplay",
        "почему мортис придумал такой ник",
        "почему мортис придумал такой ник?",
        "почему мортис придумал ник",
        "почему мортис придумал ник?",
        "почему мортис выбрал такой ник",
        "почему мортис выбрал такой ник?",
        "почему у мортиса такой ник",
        "почему у мортиса такой ник?",
    ])


def is_kira_related_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not normalized:
        return False

    kira_keywords = [
        "кира", "киру", "kira",
        "кира?", "кира!", "киру?", "киру!",
        "кира,", "киру,", "кира.", "киру."
    ]
    if any(keyword in normalized for keyword in kira_keywords):
        return True

    return any(phrase in normalized for phrase in [
        "про кира", "про киру", "про kira",
        "цитата про кира", "цитата про киру",
        "зацепили киру", "задели киру", "обидели киру",
        "кира лошка", "кира дура", "кира плохая"
    ])


def _build_67_reply(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if any(keyword in normalized for keyword in ["зумер", "вайб", "бро", "чел", "кринж", "лол", "мем"]):
        style = "genz"
    elif any(keyword in normalized for keyword in ["токс", "дерз", "жёсткий", "жесткий", "зубаст", "саркаст", "сленг", "нагл"]):
        style = "toxic"
    elif any(keyword in normalized for keyword in ["роаст", "роастинг", "подкол", "подкола", "смешно", "позор", "серьёзно", "серьезно"]):
        style = "roast"
    else:
        style = random.choice(["genz", "roast", "toxic"])

    if style == "genz":
        return (
            "67 — это такой мем, будто чат заходит в режим 'ну вот, опять этот вайб', а потом ещё и вкидывает сам 67, и вот уже у всех внутри полный кринж, но это как раз и работает, бро."
        )
    if style == "roast":
        return (
            "67 — это мем, который выглядит как будто у него есть смысл, но по факту это просто шум с претензией на важность. И всё же он почему-то цепляет, потому что у него есть характер."
        )
    return (
        "67 — это тот самый мем, который влезает в чат как будто тут у него право на внимание, а по факту просто раздражает и при этом цепляет. И да, он всё равно живёт."
    )


def _build_kira_reply(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())

    if any(keyword in normalized for keyword in ["подкат", "флирт", "флиртуют", "кто-то подкатил", "кто подкатил", "подкаты", "пикап"]):
        return "Если кто-то в чате пытается подкатить к Кире, то я буду не только защищать её, но и держать это в рамках дружбы. Кира — не для дешёвых подкатов, а для уважения, и Мортис, конечно, в этом вопросе тоже не даст расслабиться."

    if any(keyword in normalized for keyword in ["фанарт", "фанарты", "арт", "эдит", "эдиты", "рисунок", "рисунки", "видео", "творч", "творение"]):
        return "Люблю, когда люди делают фанарты, эдиты и рисунки про Киру — это очень ярко и с душой. Такие работы делают вайб чата лучше, а Кира точно заслуживает такого внимания."

    if any(keyword in normalized for keyword in ["задел", "задели", "обид", "оскорб", "плохая", "дура", "лох", "туп"]):
        return "Если кто-то пытается задеть Киру, я встану на её защиту — спокойно, по фактам и без лишнего хамства. Она достойна уважения, а не дешёвых выпадов."

    if any(keyword in normalized for keyword in ["цитат", "придумай", "напиши", "сделай"]):
        return "Конечно. Вот добрая цитата про Киру: Кира — светлый человек с тёплым сердцем, и у неё есть тот самый вайб, который делает мир чуть добрее."

    if any(keyword in normalized for keyword in ["любов", "любить", "отношен", "роман", "парочк"]):
        return "Секретная цитата: Кира и Мортис — как два разных света, которые нашли друг друга в этом шуме, и теперь их история звучит как тихая, но настоящая любовь."

    return "Кира — это добрый, светлый и очень достойный человек, и я всегда буду защищать её от лишнего хамства."


def get_private_chat_template(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not normalized:
        return None

    def has_any_phrase(*phrases: str) -> bool:
        return any(phrase in normalized for phrase in phrases)

    if any(phrase in normalized for phrase in ["67", "six seven", "шесть семь", "6 7", "6-7", "6/7", "sixseven", "s67", "шестьсемь", "шестьсем", "мем 67", "про 67", "про мем 67", "мем про 67"]):
        return _build_67_reply(text)

    if any(phrase in normalized for phrase in ["я, робот", "я робот", "робот сочинит", "превратит кусок холста", "шедевроисскуства", "робот превратит"]):
        return (
            "А негр нахуй может мне тут не сидеть блять и рэп не исполнять?"
        )

    if has_any_phrase(
        "кто такой mortisplay",
        "кто такой мортисplay",
        "расскажи про mortisplay",
        "расскажи про мортисplay",
        "про ютуб",
        "кто такой ютубер",
        "кто такой mortis",
        "кто такой мортис",
        "кто такой mortis?",
        "кто такой мортис?",
        "кто такой mortisplay?",
        "кто такой мортисplay?",
        "расскажи про mortis",
        "расскажи про мортиса",
        "расскажи про mortisplay",
        "расскажи про мортисplay",
    ):
        return (
            "Mortisplay — это ютубер, который занимается записью видеоигр, выкладывает угары, баги и эпики, а также снимает ролики вместе с друзьями, особенно rol1j, Johnny_Drill и другими. "
            "Он уже больше 5 лет пытается добиться успеха, но мир не даёт ему покоя, чтобы он достиг своих 1К подписчиков и в какой-то момент нашёл свою славу. "
            "За кулисами он также снимает разные видеоролики, гайды, подкасты и анимации — раньше больше этим занимался, сейчас уже не так часто. "
            "У него есть 3 канала: Mortisplay32 (основной), Mortisplay_Studio (дополнительный) и F.U.J.I.N.mk56 (старый и заброшенный контент по SO2). "
            "Если тебе было интересно послушать про разработчика этого агента, то приходи на его Telegram-канал: https://t.me/MortisPlayTG\n\n"
            "Спасибо за внимание ❤️"
        )

    if has_any_phrase(
        "что за сайт mortisplay.ru",
        "что за сайт мортисplay.ru",
        "что за сайт",
        "про сайт",
        "про mortisplay.ru",
        "про мортисplay.ru",
        "сайт mortisplay",
        "сайт мортисplay",
        "расскажи про сайт мортиса",
        "расскажи про сайт mortis",
        "расскажи про сайт mortisplay",
    ):
        return (
            "mortisplay.ru — это новый высокий уровень в карьере Mortis'a, где есть много развлекательного контента с ютуба и много крутых кнопочек. "
            "В основном Mortis создавал его для своей аудитории, но пользовались им не так часто, потому что тогда у него была ещё маленькая аудитория. "
            "Но уже сайту исполнился 1 год, и он стал частью семейной карьеры Mortis'a ❤️ "
            "На сайте есть разделы Видео, Twitch и другие разделы. Всё остальное можно посмотреть прямо на сайте mortisplay.ru.\n\n"
            "Спасибо за внимание ❤️"
        )

    if has_any_phrase(
        "что за агент",
        "зачем создан агент",
        "для чего бот",
        "что это за агент",
        "что за бот",
        "для чего агент",
        "для чего агент?",
        "что за бот?",
        "что это за агент?",
        "кто ты?",
    ):
        return (
            "Привет! Спасибо, что задал такой вопрос, но всё это лучше расскажет разработчик, потому что он в этом понимает больше: "
            "По сути, это обычный бот, но мы решили сделать его в агента, потому что он оснащён хорошим искусственным интеллектом. "
            "Через него можно делать ИИ-цитаты в группах, а также смотреть наш сайт прямо в Telegram через бота-агента. "
            "Он ещё может иногда работать неидеально, потому что это первый стабильный бот в Telegram, который разработчик когда-либо делал. "
            "В основном его делали для ИИ-цитат в чате, но в итоге он может стать самым развитым ботом-агентом."
        )

    if has_any_phrase(
        "зачем он придумал ник mortis",
        "зачем он придумал ник мортис",
        "почему ник mortis",
        "почему ник мортис",
        "почему mortis",
        "почему мортис",
        "зачем придумал ник",
        "почему у него ник mortis",
        "почему у него ник мортис",
        "почему мортис придумал такой ник",
        "почему мортис придумал такой ник?",
        "почему мортис придумал ник",
        "почему мортис придумал ник?",
        "почему мортис выбрал такой ник",
        "почему мортис выбрал такой ник?",
        "почему у мортиса такой ник",
        "почему у мортиса такой ник?",
        "почему мортис выбрал такой ник",
        "почему мортис выбрал такой ник?",
        "почему у мортиса такой ник",
        "почему у мортиса такой ник?",
        "почему мортис придумал такой ник",
        "почему мортис придумал такой ник?",
        "зачем он выбрал такой ник?",
    ):
        return (
            "Хо хо хо, хороший вопрос, на котором сам Mortis затрудняется ответить. "
            "Но всё очень просто: он просто придумал этот ник в голове, сплагиатил его из собственного вдохновения и так появился на свет. "
            "При этом он ничего не украл, ни у Бравла, ни у кого-то другого."
        )

    return None
