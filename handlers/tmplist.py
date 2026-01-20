import asyncio
from aiogram import types
from aiogram.filters import Command

from core import dp
from helpers import (
    admin_check,
    extract_users_from_message,
    delete_command_later,
)

@dp.message(Command(commands=["tmplist", "tmlist"], ignore_case=True))
async def cmd_tmplist(msg: types.Message):
    """
    /tmplist @username UserName ...
    Создание временного списка из упоминаний.
    """

    if not await admin_check(msg):
        return

    asyncio.create_task(delete_command_later(msg))

    users = extract_users_from_message(msg)

    if not users:
        await msg.answer(
            "❌ <b>Не найдено ни одного участника.</b>\n\n"
            "Используйте:\n"
            "• <code>@username</code> (если пользователь уже был в чате)\n"
            "• или выберите пользователя из списка Telegram",
            parse_mode="HTML",
        )
        return

    unique_users = {}
    for user in users:
        unique_users[user.id] = user

    users = list(unique_users.values())

    MAX_USERS = 50
    if len(users) > MAX_USERS:
        await msg.answer(
            f"❌ Слишком много участников.\n"
            f"Максимум: {MAX_USERS}",
        )
        return

    lines = []
    for i, user in enumerate(users, start=1):
        name = user.full_name
        if user.username:
            name += f" (@{user.username})"
        lines.append(f"{i}. {name}")

    await msg.answer(
        "🧪 <b>Временный список (черновик)</b>\n\n"
        + "\n".join(lines)
        + f"\n\n👥 Всего: {len(users)}",
        parse_mode="HTML",
    )
