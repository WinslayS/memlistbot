import asyncio

from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core import bot, dp
from db import get_members, upsert_user
from helpers import format_member_inline, delete_command_later

PAGE_SIZE = 30

@dp.message(Command("list"))
async def cmd_list(msg: types.Message):
    await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)
    rows = await asyncio.to_thread(get_members, msg.chat.id)

    if not rows:
        await msg.answer("Список пуст 🕳️")
        return

    total_pages = (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE
    page = 1
    text = render_page(rows, page)

    await msg.answer(
        f"<b>📋 Список участников</b>\n\n{text}",
        parse_mode="HTML",
        reply_markup=pagination_kb(page, total_pages)
    )

    asyncio.create_task(delete_command_later(msg))

def pagination_kb(page: int, total_pages: int):
    kb = InlineKeyboardBuilder()

    if page > 1:
        kb.button(text="⬅️ Предыдущая", callback_data=f"list_page:{page-1}")

    kb.button(text=f"{page}/{total_pages}", callback_data="noop")

    if page < total_pages:
        kb.button(text="Следующая ➡️", callback_data=f"list_page:{page+1}")

    kb.adjust(3)
    return kb.as_markup()

def render_page(rows: list, page: int):
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE

    chunk = rows[start:end]

    lines = [
        format_member_inline(row, start + i + 1)
        for i, row in enumerate(chunk)
    ]

    return "\n".join(lines)

@dp.callback_query(lambda c: c.data.startswith("list_page:"))
async def list_pagination(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[1])

    rows = await asyncio.to_thread(get_members, callback.message.chat.id)
    total_pages = (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE

    text = render_page(rows, page)

    await callback.message.edit_text(
        f"<b>📋 Список участников</b>\n\n{text}",
        parse_mode="HTML",
        reply_markup=pagination_kb(page, total_pages)
    )

    await callback.answer()

@dp.callback_query(lambda c: c.data == "noop")
async def noop_callback(callback: types.CallbackQuery):
    await callback.answer()

@dp.message(Command("find"))
async def cmd_find(msg: types.Message):
    args = msg.text.split(maxsplit=1)

    if len(args) < 2:
        await msg.answer("Использование: /find часть_имени или @username")
        return

    raw_query = args[1].strip()
    query = raw_query.lstrip("@").lower()

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
        await msg.answer(f"❌ Ничего не найдено по запросу: <i>{raw_query}</i>", parse_mode="HTML")
        asyncio.create_task(delete_command_later(msg, delay=6))
        return

    lines = [
        format_member_inline(row, i + 1)
        for i, row in enumerate(results)
    ]
    full_text = "\n".join(lines)

    safe_query = raw_query.replace("<", "&lt;").replace(">", "&gt;")
    header = f"🔎 <b>Результаты поиска:</b> <i>{safe_query}</i>"

    await msg.answer(
        f"{header}\n\n{full_text}",
        parse_mode="HTML"
    )

    asyncio.create_task(delete_command_later(msg, delay=6))
