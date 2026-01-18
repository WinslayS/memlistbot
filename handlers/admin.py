import asyncio
import time
import csv
import io

from aiogram import types
from aiogram.filters import Command
from aiogram.types import BufferedInputFile

from core import bot, dp
from logger import logger
from db import supabase, upsert_user, get_members, clear_left_users
from helpers import (
    admin_check, find_user_by_target, show_user_selection,
    format_member_txt
)

# ========== ADMIN: SET NAME FOR ANOTHER USER ==========

@dp.message(Command("setname"))
async def admin_set_name(msg: types.Message):
    if not await admin_check(msg):
        return

    # ================= REPLY MODE =================
    if msg.reply_to_message and msg.reply_to_message.from_user:
        target_user = msg.reply_to_message.from_user

        logger.info(
            "SETNAME: reply mode | admin=%s target=%s",
            msg.from_user.id,
            target_user.id
        )

        args = msg.text.split(maxsplit=1)
        if len(args) < 2:
            await msg.answer(
                "❌ Напишите имя.\n"
                "Пример:\n"
                "/setname Иван"
            )
            return

        new_name = args[1].strip()
        if not new_name:
            await msg.answer("❌ Имя не может быть пустым.")
            return

        await asyncio.to_thread(upsert_user, msg.chat.id, target_user)

        supabase.table("members") \
            .update({"external_name": new_name}) \
            .eq("chat_id", msg.chat.id) \
            .eq("user_id", target_user.id) \
            .execute()

        await msg.answer(
            f"✨ Имя участника <b>{target_user.full_name}</b> обновлено на <b>{new_name}</b>",
            parse_mode="HTML"
        )
        return

    # ================= TEXT MODE =================
    args = msg.text.split(maxsplit=2)

    if len(args) < 3:
        await msg.answer(
            "❌ Неверный формат.\n\n"
            "Правильно:\n"
            "/setname @username Имя\n"
            "/setname user_id Имя\n"
            "/setname ПолноеИмя Имя\n\n"
            "Или ответом на сообщение:\n"
            "/setname Имя"
        )
        return

    target = args[1].strip()
    new_name = args[2].strip()

    logger.info(
        "SETNAME: text mode | admin=%s target_raw='%s' new_name='%s'",
        msg.from_user.id,
        target,
        new_name
    )

    if not new_name:
        await msg.answer("❌ Имя не может быть пустым.")
        return

    found_user = await find_user_by_target(msg.chat.id, target)

    if found_user == "MULTIPLE":
        rows = await asyncio.to_thread(get_members, msg.chat.id)
        target_lower = target.lower()
        matches = [
            m for m in rows
            if target_lower in (m.get("full_name") or "").lower()
            or target_lower in (m.get("external_name") or "").lower()
            or target_lower in (m.get("username") or "").lower()
        ]
        await show_user_selection(msg, matches, "name", new_name)
        return

    if not found_user:
        await msg.answer("❌ Участник не найден в базе.")
        return

    uid = found_user["user_id"]

    supabase.table("members") \
        .update({"external_name": new_name}) \
        .eq("chat_id", msg.chat.id) \
        .eq("user_id", uid) \
        .execute()

    await msg.answer(
        f"✨ Имя участника обновлено на <b>{new_name}</b>",
        parse_mode="HTML"
    )

# ========== ADMIN ADDROLE ==========

@dp.message(Command("addrole"))
async def admin_add_role(msg: types.Message):
    if not await admin_check(msg):
        return

    # --- 1) РЕЖИМ ЧЕРЕЗ REPLY ---
    if msg.reply_to_message and msg.reply_to_message.from_user:
        target = msg.reply_to_message.from_user

        logger.info(
            "ADDROLE: reply mode | admin=%s target=%s",
            msg.from_user.id,
            target.id
        )

        args = msg.text.split(maxsplit=1)

        if len(args) < 2:
            await msg.answer("Напишите роль. Пример:\n/addrole Руководитель")
            return

        role = args[1].strip()
        if not role:
            await msg.answer("❌ Роль не может быть пустой.")
            return
            
        # удаляем случайно попавший @username из роли
        if target.username:
            role = role.replace(f"@{target.username}", "").strip()

        # удаляем ВСЕ слова, начинающиеся на @ (универсально)
        role = " ".join(word for word in role.split() if not word.startswith("@"))

        try:
            (
                supabase.table("members")
                .update({"extra_role": role})
                .eq("chat_id", msg.chat.id)
                .eq("user_id", target.id)
                .execute()
            )
        except Exception as e:
            logger.error("Supabase addrole(reply) update error: %s", e)
            await msg.answer("⚠ Произошла ошибка при сохранении роли.")
            return

        await msg.answer(
            f"✨ Роль участника <b>{target.full_name}</b> обновлена на <b>{role}</b>",
            parse_mode="HTML"
        )
        return

    # --- 2) РЕЖИМ ЧЕРЕЗ ТЕКСТ ---
    args = msg.text.split(maxsplit=2)
    if len(args) < 3:
        await msg.answer(
            "Форматы:\n"
            "/addrole @username Роль\n"
            "/addrole user_id Роль\n"
            "/addrole Имя Роль\n"
            "ИЛИ ответом на сообщение:\n"
            "/addrole Роль"
        )
        return

    target = args[1].strip()
    role = args[2].strip()

    logger.info(
        "ADDROLE: text mode | admin=%s target_raw='%s' role='%s'",
        msg.from_user.id,
        target,
        role
    )

    if not role:
        await msg.answer("❌ Роль не может быть пустой.")
        return

    # 1) сначала найдём пользователя
    found_user = await find_user_by_target(msg.chat.id, target)

    if found_user == "MULTIPLE":
        matches = await asyncio.to_thread(get_members, msg.chat.id)

        target_lower = target.lower()
        filtered = [
            m for m in matches
            if target_lower in (m.get("full_name") or "").lower()
            or target_lower in (m.get("external_name") or "").lower()
            or target_lower in (m.get("username") or "").lower()
        ]

        await show_user_selection(msg, filtered, "role", role)
        return


    if not found_user:
        await msg.answer("❌ Пользователь не найден.")
        return

    # 2) очищаем роль от @username
    uname = found_user.get("username")
    if uname:
        role = role.replace(f"@{uname}", "").strip()

    # 3) удаляем любые случайные @ слова
    role = " ".join(word for word in role.split() if not word.startswith("@"))

    uid = found_user["user_id"]

    # 4) обновляем роль
    try:
        (
            supabase.table("members")
            .update({"extra_role": role})
            .eq("chat_id", msg.chat.id)
            .eq("user_id", uid)
            .execute()
        )
    except Exception as e:
        logger.error("Supabase addrole(update) error: %s", e)
        await msg.answer("⚠ Ошибка при обновлении роли.")
        return

    await msg.answer(
        f"✨ Роль участника обновлена на <b>{role}</b>",
        parse_mode="HTML"
    )

# ========== ADMIN EXPORT CSV ==========

import csv
import io
from aiogram.types import BufferedInputFile

@dp.message(Command("export"))
async def cmd_export(msg: types.Message):
    if not await admin_check(msg):
        return

    rows = await asyncio.to_thread(get_members, msg.chat.id)

    if not rows:
        await msg.answer("Список пуст, нечего экспортировать.")
        return

    # === определяем сортировку ===
    args = msg.text.split()
    sort_mode = args[1].lower() if len(args) > 1 else None

    if sort_mode in ["name", "n"]:               # сортировка по full_name
        rows.sort(key=lambda r: (r.get("full_name") or "").lower())

    elif sort_mode in ["username", "user", "u"]: # сортировка по username
        rows.sort(key=lambda r: (r.get("username") or "").lower())

    elif sort_mode in ["external", "ext", "e"]:  # сортировка по external_name
        rows.sort(key=lambda r: (r.get("external_name") or "").lower())

    # === формируем TXT-файл ===
    output = io.StringIO()
    output.write("📋 Список участников:\n\n")

    for i, row in enumerate(rows, start=1):
        line = format_member_txt(row, i)
        output.write(line + "\n")

    csv_bytes = output.getvalue().encode("utf-8")

    file = BufferedInputFile(
        file=csv_bytes,
        filename=f"members_chat_{msg.chat.id}.txt"
    )

    await msg.answer_document(file, caption="📄 Экспортирован список участников.")

# ========== CLEANUP (удаление ушедших) ==========

@dp.message(Command("cleanup"))
async def cmd_cleanup(msg: types.Message):
    if not await admin_check(msg):
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
            # пользователь недоступен в TG → точно нет в чате
            left_users.append(uid)
            continue

        # === Пользователь вышел ===
        if status in ("left", "kicked"):
            left_users.append(uid)
            continue

        # === Пользователь в чате → обновляем данные ===
        tg_user = member.user

        new_username = tg_user.username or ""
        new_fullname = tg_user.full_name or ""

        # изменения?
        changed = (
            row.get("username") != new_username or
            row.get("full_name") != new_fullname
        )

        if changed:
            updated_users += 1
            try:
                await asyncio.to_thread(upsert_user, msg.chat.id, tg_user)
                supabase.table("members").update({
                    "username": new_username,
                    "full_name": new_fullname
                }).eq("chat_id", msg.chat.id).eq("user_id", uid).execute()
            except Exception as e:
                logger.error("Ошибка обновления пользователя %s: %s", uid, e)

    # === Удаляем ушедших ===
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
