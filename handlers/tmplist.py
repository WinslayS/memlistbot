import asyncio, re
from aiogram import types
from aiogram.filters import Command

from datetime import datetime, timedelta, timezone
from db import supabase

from core import bot, dp
from helpers import (
    admin_check,
    extract_users_from_message,
    delete_command_later,
    make_silent_username
)

MAX_USERS = 50
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$", re.I)

@dp.message(Command(commands=["tmplist", "tmlist"], ignore_case=True))
async def cmd_tmplist(msg: types.Message):

    if not await admin_check(bot, msg):
        return

    args = msg.text.split()
    if len(args) < 2:
        await msg.answer("❌ Укажи название списка.\nПример: /tmplist raid1 @user")
        return

    list_name = args[1].lower()

    if list_name.startswith("@") or not NAME_RE.match(list_name):
        await msg.answer(
            "❌ Неверное имя списка.\n"
            "Пример: <code>raid1</code> или <code>defense_team</code>",
            parse_mode="HTML"
        )
        return

    chat_id = msg.chat.id

    deactivate_expired_tmplists(chat_id)

    res = (
        supabase
        .table("tmplists")
        .select("id")
        .eq("chat_id", chat_id)
        .eq("name", list_name)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )

    tmplist_id = res.data[0]["id"] if res.data else None
    is_new_list = tmplist_id is None

    if is_new_list:
        if count_active_tmplists(chat_id) >= 3:
            await msg.answer(
                "❌ <b>Достигнут лимит временных списков.</b>\n\n"
                "Максимум: <b>3 активных списка</b> на группу.\n"
                "⏱ Каждый список живёт 24 часа.",
                parse_mode="HTML"
            )
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
            name += f" ({make_silent_username(user.username)})"
        lines.append(f"{i}. {name}")

    if is_new_list:
        tmplist_id = create_tmplist(
            chat_id=msg.chat.id,
            created_by=msg.from_user.id,
            name=list_name,
        )

    added_count = insert_tmplist_items(tmplist_id, [u.id for u in users])

    if added_count == 0:
        footer = "ℹ️ Все указанные пользователи уже были в списке"
    else:
        footer = f"👥 Добавлено: {added_count}"

    title = (
        "🆕 <b>Временный список создан</b>"
        if is_new_list
        else "➕ <b>Участники добавлены в список</b>"
    )

    sent = await msg.answer(
        f"{title} <b>{list_name}</b>\n\n"
        + "\n".join(lines)
        + f"\n\n{footer}",
        parse_mode="HTML",
    )

def create_tmplist(
    chat_id: int,
    created_by: int,
    name: str,
    message_id: int | None = None,
) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    res = (
        supabase
        .table("tmplists")
        .insert({
            "chat_id": chat_id,
            "created_by": created_by,
            "expires_at": expires_at.isoformat(),
            "message_id": message_id,
            "name": name,
        })
        .execute()
    )

    return res.data[0]["id"]

def insert_tmplist_items(tmplist_id: str, user_ids: list[int]) -> int:
    rows = [{"tmplist_id": tmplist_id, "user_id": uid} for uid in user_ids]
    if not rows:
        return 0

    res = supabase.table("tmplist_items").insert(rows).execute()
    return len(res.data or [])

def deactivate_expired_tmplists(chat_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    (
        supabase.table("tmplists")
        .update({"is_active": False})
        .eq("chat_id", chat_id)
        .eq("is_active", True)
        .lte("expires_at", now)
        .execute()
    )

def count_active_tmplists(chat_id: int) -> int:
    now = datetime.now(timezone.utc).isoformat()
    res = (
        supabase
        .table("tmplists")
        .select("id", count="exact")
        .eq("chat_id", chat_id)
        .eq("is_active", True)
        .gt("expires_at", now)
        .execute()
    )
    return res.count or 0

@dp.message(Command(commands=["tmplists"], ignore_case=True))
async def cmd_tmplists(msg: types.Message):
    if not await admin_check(bot, msg):
        return

    chat_id = msg.chat.id

    deactivate_expired_tmplists(chat_id)

    res = (
        supabase
        .table("tmplists")
        .select("name, expires_at, created_by")
        .eq("chat_id", chat_id)
        .eq("is_active", True)
        .order("expires_at")
        .execute()
    )

    if not res.data:
        await msg.answer("ℹ️ Активных временных списков нет.")
        return

    lines = ["📋 <b>Активные временные списки:</b>\n"]
    now = datetime.now(timezone.utc)

    for row in res.data:
        expires = datetime.fromisoformat(row["expires_at"])
        remaining = expires - now
        hours = int(remaining.total_seconds() // 3600)

        lines.append(
            f"• <b>{row['name']}</b> — ⏱ {hours}ч осталось"
        )

    await msg.answer("\n".join(lines), parse_mode="HTML")

@dp.message(Command(commands=["tmplist_show"], ignore_case=True))
async def cmd_tmplist_show(msg: types.Message):
    if not await admin_check(bot, msg):
        return

    args = msg.text.split()
    if len(args) < 2:
        await msg.answer("❌ Укажи имя списка.")
        return

    list_name = args[1].lower()
    chat_id = msg.chat.id

    deactivate_expired_tmplists(chat_id)

    res = (
        supabase
        .table("tmplists")
        .select("id")
        .eq("chat_id", chat_id)
        .eq("name", list_name)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )

    if not res.data:
        await msg.answer("❌ Активный список не найден.")
        return

    tmplist_id = res.data[0]["id"]

    users = (
        supabase
        .table("tmplist_items")
        .select("user_id")
        .eq("tmplist_id", tmplist_id)
        .execute()
        .data
    )

    await msg.answer(
        f"📄 Список <b>{list_name}</b>\n"
        f"👥 Участников: {len(users)}",
        parse_mode="HTML"
    )

@dp.message(Command(commands=["tmplist_delete"], ignore_case=True))
async def cmd_tmplist_delete(msg: types.Message):
    if not await admin_check(bot, msg):
        return

    args = msg.text.split()
    if len(args) < 2:
        await msg.answer("❌ Укажи имя списка для удаления.")
        return

    list_name = args[1].lower()
    chat_id = msg.chat.id

    deactivate_expired_tmplists(chat_id)

    res = (
        supabase
        .table("tmplists")
        .update({"is_active": False})
        .eq("chat_id", chat_id)
        .eq("name", list_name)
        .eq("is_active", True)
        .execute()
    )

    if not res.data:
        await msg.answer("❌ Активный список не найден.")
        return

    await msg.answer(
        f"🗑 Список <b>{list_name}</b> удалён.",
        parse_mode="HTML"
    )

@dp.message(Command(commands=["tmplist_remove"], ignore_case=True))
async def cmd_tmplist_remove(msg: types.Message):
    if not await admin_check(bot, msg):
        return

    args = msg.text.split()
    if len(args) < 3:
        await msg.answer(
            "❌ Использование:\n"
            "<code>/tmplist_remove raid1 @user</code>",
            parse_mode="HTML"
        )
        return

    list_name = args[1].lower()
    chat_id = msg.chat.id

    deactivate_expired_tmplists(chat_id)

    res = (
        supabase
        .table("tmplists")
        .select("id")
        .eq("chat_id", chat_id)
        .eq("name", list_name)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )

    if not res.data:
        await msg.answer("❌ Активный список не найден.")
        return

    tmplist_id = res.data[0]["id"]

    users = extract_users_from_message(msg)
    if not users:
        await msg.answer("❌ Не указаны пользователи для удаления.")
        return

    user_ids = list({u.id for u in users})

    (
        supabase
        .table("tmplist_items")
        .delete()
        .eq("tmplist_id", tmplist_id)
        .in_("user_id", user_ids)
        .execute()
    )

    await msg.answer(
        f"🧹 Удалено пользователей: {len(user_ids)} из списка <b>{list_name}</b>",
        parse_mode="HTML"
    )
