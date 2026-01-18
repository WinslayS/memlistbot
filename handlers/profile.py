import asyncio

from aiogram import types
from aiogram.filters import Command

from core import dp
from logger import logger
from db import supabase, upsert_user

# ========== NAME ==========

@dp.message(Command("name"))
async def cmd_name(msg: types.Message):
    args = msg.text.split(maxsplit=1)

    if len(args) < 2:
        await msg.answer("✏️ Напиши имя после команды. Пример: /name Kvane")
        return

    external_name = args[1].strip()

    # пустое имя (только пробелы)
    if not external_name:
        await msg.answer("❌ Имя не может быть пустым или состоять только из пробелов.")
        return

    # лимит длины 100 символов
    if len(external_name) > 100:
        await msg.answer("❌ Имя слишком длинное. Максимум 100 символов.")
        return

    await asyncio.to_thread(
        upsert_user,
        msg.chat.id,
        msg.from_user,
        external_name
    )

    await msg.answer(
        f"✅ Имя установлено: <b>{external_name}</b>",
        parse_mode="HTML"
    )

# ========== ADD ==========

@dp.message(Command("add"))
async def cmd_add(msg: types.Message):
    args = msg.text.split(maxsplit=1)

    if len(args) < 2:
        await msg.answer("Напишите роль. Пример:\n/add Работник")
        return

    role = args[1].strip()
    if not role:
        await msg.answer("❌ Роль не может быть пустой.")
        return

    try:
        (
            supabase.table("members")
            .update({"extra_role": role})
            .eq("chat_id", msg.chat.id)
            .eq("user_id", msg.from_user.id)
            .execute()
        )
    except Exception as e:
        logger.error("Supabase add (self) error: %s", e)
        await msg.answer("⚠ Ошибка при сохранении.")
        return

    await msg.answer(f"✅ Роль установлена: <b>{role}</b>", parse_mode="HTML")
