import asyncio
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

@dp.message(Command(commands=["tmplist", "tmlist"], ignore_case=True))
async def cmd_tmplist(msg: types.Message):
    """
    /tmplist @username UserName ...
    Создание временного списка из упоминаний.
    """

    if not await admin_check(bot, msg):
        return

    chat_id = msg.chat.id

    deactivate_expired_tmplists(chat_id)

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
            name += f" ({make_silent_username(user.username)})"
        lines.append(f"{i}. {name}")

    sent = await msg.answer(
        "🧪 <b>Временный список (черновик)</b>\n\n"
        + "\n".join(lines)
        + f"\n\n👥 Всего: {len(users)}",
        parse_mode="HTML",
    )

    tmplist_id = create_tmplist(
        chat_id=msg.chat.id,
        created_by=msg.from_user.id,
        message_id=sent.message_id,
    )

    insert_tmplist_items(tmplist_id, [u.id for u in users])

def create_tmplist(chat_id: int, created_by: int, message_id: int) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    res = (
        supabase
        .table("tmplists")
        .insert({
            "chat_id": chat_id,
            "created_by": created_by,
            "expires_at": expires_at.isoformat(),
            "message_id": message_id,
        })
        .execute()
    )

    return res.data[0]["id"]

def insert_tmplist_items(tmplist_id: str, user_ids: list[int]) -> None:
    rows = [{"tmplist_id": tmplist_id, "user_id": uid} for uid in user_ids]
    if not rows:
        return
    supabase.table("tmplist_items").insert(rows).execute()

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
