import asyncio
import csv
import io

from aiogram import types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile

from core import bot, dp
from logger import logger
from db import supabase, upsert_user, get_members, clear_left_users
from helpers import (
    admin_check,
    format_member_txt,
    get_target_user_from_reply,
    auto_delete
)

@dp.message(Command("setname"))
@auto_delete()
async def admin_set_name(msg: types.Message):
    if not await admin_check(bot, msg):
        return

    target_user = get_target_user_from_reply(msg)
    if not target_user:
        await msg.answer(
            "❌ Ответьте на сообщение конкретного пользователя.\n\n"
            "Поддерживаются:\n"
            "• обычные сообщения пользователя\n"
            "• сообщения о входе пользователя в чат\n\n"
            "⚠️ Если в одном сообщении добавлено несколько участников — "
            "дождитесь, пока нужный пользователь напишет сообщение.",
            parse_mode="HTML"
        )
        return

    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer(
            "❌ Напишите имя.\n\n"
            "Пример (ответом на сообщение):\n"
            "<code>/setname Иван</code>",
            parse_mode="HTML"
        )
        return

    new_name = args[1].strip()
    if not new_name:
        await msg.answer("❌ Имя не может быть пустым.")
        return

    await asyncio.to_thread(upsert_user, msg.chat.id, target_user)

    try:
        (
            supabase.table("members")
            .update({"external_name": new_name})
            .eq("chat_id", msg.chat.id)
            .eq("user_id", target_user.id)
            .execute()
        )
    except Exception as e:
        logger.error("Supabase setname update error: %s", e)
        await msg.answer("⚠ Произошла ошибка при сохранении имени.")
        return

    await msg.answer(
        f"✨ Имя участника <b>{target_user.full_name}</b> обновлено на <b>{new_name}</b>",
        parse_mode="HTML"
    )

@dp.message(Command("addrole"))
@auto_delete()
async def admin_add_role(msg: types.Message):
    if not await admin_check(bot, msg):
        return

    target_user = get_target_user_from_reply(msg)
    if not target_user:
        await msg.answer(
            "❌ Ответьте на сообщение конкретного пользователя.\n\n"
            "Поддерживаются:\n"
            "• обычные сообщения пользователя\n"
            "• сообщения о входе пользователя в чат",
            parse_mode="HTML"
        )
        return

    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer(
            "❌ Напишите роль.\n\n"
            "Пример (ответом на сообщение):\n"
            "<code>/addrole Руководитель</code>",
            parse_mode="HTML"
        )
        return

    role = args[1].strip()
    if not role:
        await msg.answer("❌ Роль не может быть пустой.")
        return

    role = " ".join(word for word in role.split() if not word.startswith("@"))

    await asyncio.to_thread(upsert_user, msg.chat.id, target_user)

    try:
        (
            supabase.table("members")
            .update({"extra_role": role})
            .eq("chat_id", msg.chat.id)
            .eq("user_id", target_user.id)
            .execute()
        )
    except Exception as e:
        logger.error("Supabase addrole update error: %s", e)
        await msg.answer("⚠ Произошла ошибка при сохранении роли.")
        return

    await msg.answer(
        f"✨ Роль участника <b>{target_user.full_name}</b> обновлена на <b>{role}</b>",
        parse_mode="HTML"
    )

@dp.message(Command("export"))
@auto_delete()
async def cmd_export(msg: types.Message):
    if not await admin_check(bot, msg):
        return

    rows = await asyncio.to_thread(get_members, msg.chat.id)
    if not rows:
        await msg.answer("Список пуст, нечего экспортировать.")
        return

    args = msg.text.split()
    sort_mode = args[1].lower() if len(args) > 1 else None

    if sort_mode in ["name", "n"]:
        rows.sort(key=lambda r: (r.get("full_name") or "").lower())
    elif sort_mode in ["username", "user", "u"]:
        rows.sort(key=lambda r: (r.get("username") or "").lower())
    elif sort_mode in ["external", "ext", "e"]:
        rows.sort(key=lambda r: (r.get("external_name") or "").lower())

    output = io.StringIO()
    output.write("📋 Список участников:\n\n")

    for i, row in enumerate(rows, start=1):
        output.write(format_member_txt(row, i) + "\n")

    file = BufferedInputFile(
        file=output.getvalue().encode("utf-8"),
        filename=f"members_chat_{msg.chat.id}.txt"
    )

    await msg.answer_document(file, caption="📄 Экспортирован список участников.")

@dp.message(Command("cleanup"))
@auto_delete()
async def cmd_cleanup(msg: types.Message):
    if not await admin_check(bot, msg):
        return

    rows = await asyncio.to_thread(get_members, msg.chat.id)
    left_users = []
    updated_users = 0

    for row in rows:
        uid = row["user_id"]

        try:
            member = await bot.get_chat_member(msg.chat.id, uid)
            status = member.status
        except Exception:
            left_users.append(uid)
            continue

        if status in ("left", "kicked"):
            left_users.append(uid)
            continue

        tg_user = member.user
        new_username = tg_user.username or ""
        new_fullname = tg_user.full_name or ""

        if (
            row.get("username") != new_username or
            row.get("full_name") != new_fullname
        ):
            updated_users += 1
            try:
                await asyncio.to_thread(upsert_user, msg.chat.id, tg_user)
                (
                    supabase.table("members")
                    .update({
                        "username": new_username,
                        "full_name": new_fullname
                    })
                    .eq("chat_id", msg.chat.id)
                    .eq("user_id", uid)
                    .execute()
                )
            except Exception as e:
                logger.error("Cleanup update error (%s): %s", uid, e)

    if left_users:
        await asyncio.to_thread(clear_left_users, msg.chat.id, left_users)

    await msg.answer(
        f"🧹 <b>Очистка завершена!</b>\n"
        f"Удалено: <b>{len(left_users)}</b>\n"
        f"Обновлено: <b>{updated_users}</b>",
        parse_mode="HTML"
    )

    logger.info(
        "Cleanup finished: removed=%s updated=%s chat=%s",
        len(left_users), updated_users, msg.chat.id
    )
