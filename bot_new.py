import asyncio

import bot_config
import storage
import handlers  # noqa: F401
import callbacks  # noqa: F401
import interactions  # noqa: F401


async def main() -> None:
    print("Бот запущен на OpenRouter с дизайном QuotLy! Погнали!")
    storage.load_quote_stats()
    storage.load_chat_settings()

    if bot_config.bot is None:
        raise RuntimeError("BOT_TOKEN is not configured in environment.")

    try:
        me = await bot_config.bot.get_me()
        bot_config.BOT_USERNAME = me.username if me and getattr(me, "username", None) else None
        print(f"Bot username: @{bot_config.BOT_USERNAME}" if bot_config.BOT_USERNAME else "Bot username неизвестен")
    except Exception as exc:
        print("Не удалось получить username бота:", exc)

    await bot_config.dp.start_polling(bot_config.bot)


if __name__ == "__main__":
    asyncio.run(main())
