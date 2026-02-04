import asyncio

from aiogram import types
from aiogram.filters import Command

from core import dp
from logger import logger
from db import supabase, upsert_user
from helpers import (
    auto_delete,
    answer_temp
)
    
MAX_LEN = 100

@dp.message(Command("name"))
@auto_delete()
async def cmd_name(msg: types.Message):
    args = msg.text.split(maxsplit=1)

    if len(args) < 2:
        await answer_temp(
            msg,
            "✏️ Напиши имя после команды. Пример: /name Kvane"
        )
        return

    external_name = args[1].strip()

    if not external_name:
        await answer_temp(
            msg,
            "❌ Имя не может быть пустым или состоять только из пробелов."
        )
        return

    if len(external_name) > MAX_LEN:
        await answer_temp(
            msg,
            "❌ Имя слишком длинное. Максимум {MAX_LEN} символов."
        )
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

@dp.message(Command("add"))
@auto_delete()
async def cmd_add(msg: types.Message):
    args = msg.text.split(maxsplit=1)

    if len(args) < 2:
        await answer_temp(
            msg,
            "Напишите роль. Пример:\n/add Работник"
        )
        return

    role = args[1].strip()
    if not role:
        await answer_temp(
            msg,
            "❌ Роль не может быть пустой или состоять только из пробелов."
        )
        return

    if len(role) > MAX_LEN:
        await answer_temp(
            msg,
            f"❌ Роль слишком длинная. Максимум {MAX_LEN} символов."
        )
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
