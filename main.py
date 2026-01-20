import asyncio
from aiogram import types

from core import bot, dp

import handlers

async def main():
    print("BOT STARTED OK")

    # === Регистрируем команды в Telegram ===
    await bot.set_my_commands([
        types.BotCommand(command="help", description="Помощь / команды"),
        types.BotCommand(command="list", description="Показать список участников"),
        types.BotCommand(command="name", description="Установить своё имя"),
        types.BotCommand(command="add", description="Установить себе роль"),
        types.BotCommand(command="find", description="Поиск участника"),
        types.BotCommand(command="setname", description="Установить имя другому (админ)"),
        types.BotCommand(command="addrole", description="Назначить роль участнику (админ)"),
        types.BotCommand(command="export", description="Экспорт списка (админ)"),
        types.BotCommand(command="cleanup", description="Очистка списка (админ)"),
        types.BotCommand(command="tmplist", description="Временный список (админ)")
    ])

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
