import asyncio

from aiogram import types
from aiogram.filters import Command

from core import bot, dp
from db import get_members, upsert_user
from helpers import send_long_message, format_member_inline

# ============ COMMANDS ============

@dp.message(Command("list"))
async def cmd_list(msg: types.Message):
    await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)
    rows = await asyncio.to_thread(get_members, msg.chat.id)

    if not rows:
        await msg.answer("Список пуст 🕳️")
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
        
    # === создаём строки ===
    lines = []
    for i, row in enumerate(rows, start=1):
        lines.append(format_member_inline(row, i))

    full_text = "\n".join(lines)
    await send_long_message(bot, msg, "📋 Список участников", full_text)

# ========== FIND USER ==========

@dp.message(Command("find"))
async def cmd_find(msg: types.Message):
    args = msg.text.split(maxsplit=1)

    if len(args) < 2:
        await msg.answer("Использование: /find часть_имени или @username")
        return

    query = args[1].lstrip("@").strip().lower()
    rows = await asyncio.to_thread(get_members, msg.chat.id)

    results = []
    for row in rows:
        full_name = (row.get("full_name") or "").lower()
        username = (row.get("username") or "").lower()
        external = (row.get("external_name") or "").lower()
        role = (row.get("extra_role") or "").lower()

        if query in full_name or query in username or query in external or query in role:
            results.append(row)

    if not results:
        await msg.answer("❌ Никто не найден.")
        return

    lines = [format_member_inline(r, i+1) for i, r in enumerate(results)]
    full_text = "\n".join(lines)

    await send_long_message(bot, msg, "🔎 Результаты поиска", full_text)
