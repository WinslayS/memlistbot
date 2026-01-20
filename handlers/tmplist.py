from aiogram import types
from aiogram.filters import Command

from core import dp
from helpers import admin_check, extract_users_from_message
print("TMPLIST HANDLER LOADED")

@dp.message(Command("tmplist"))
async def cmd_tmplist(msg: types.Message):
    if not await admin_check(msg):
        return

    users = extract_users_from_message(msg)

    if not users:
        await msg.answer("❌ ...")
        return

    await msg.answer(f"👥 Найдено участников: {len(users)}")
