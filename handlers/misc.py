import asyncio
import time

from aiogram import types
from aiogram.filters import Command

from core import dp
from logger import logger
from db import supabase, upsert_user
from helpers import (
    is_user_admin, get_admin_ids,
    LAST_UPDATE, UPDATE_TTL, PENDING_ACTIONS
)

# ============ COMMANDS ============

@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)

    role = "Админ" if await is_user_admin(msg) else "Участник"

    await msg.answer(
        (
            f"👋 Привет! Ваша роль: <b>{role}</b>\n\n"
            "📌 <b>Команды:</b>\n"
            "/list — показать список участников\n"
            "/name [имя] — задать своё имя\n"
            "/find [имя/@] — поиск участника\n"
            "/setname [@] [имя] — назначить имя другому (админ)\n"
            "/export — экспорт списка (админ)\n"
            "/cleanup — очистить список ушедших (админ)\n"
            "/add [роль] — установить себе роль (участник)\n"
            "/addrole [@] [роль] — назначить роль другому участнику (админ)\n\n"
            "📖 <b>Как добавить участника:</b>\n"
            "• Если есть username (@) в базе данных (автоматически при заходе):\n"
            "  <code>/setname @username Имя</code>\n\n"
            "• Если <b>username нет</b>, его можно добавить <u>только</u> так:\n"
            "  1) он должен написать любое сообщение в чат\n"
            "  2) вы отвечаете на его сообщение командой:\n"
            "     <code>/setname Имя</code>\n\n"
            "• Если участник хочет сам добавить себе имя — он пишет:\n"
            "  <code>/name Имя</code>\n\n"
            "📖 <b>Обозначения:</b>\n"
            "• <code>[@]</code> — username участника\n"
            "• <code>[имя]</code> — любое текстовое имя\n\n"
            "📖 <b>Сортировка (добавляется к /list [], /export []:</b>\n"
            "• <b>[]</b> — по дате\n"
            "• <b>[n]</b> — по имени (full_name)\n"
            "• <b>[u]</b> — по @ (username)\n"
            "• <b>[e]</b> — по заданному имени (external_name)\n"
        ),
        parse_mode="HTML"
    )

# ============ ОБРАБОТЧИК CALLBACK ============

@dp.callback_query(lambda c: c.data.startswith("select_user:"))
async def select_user_callback(callback: types.CallbackQuery):
    task_id = callback.data.split(":", 1)[1]

    # Данные есть?
    if task_id not in PENDING_ACTIONS:
        await callback.answer("Старый или неверный выбор", show_alert=True)
        return

    data = PENDING_ACTIONS.pop(task_id)  # удаляем после использования

    chat_id = data["chat_id"]
    user_id = data["user_id"]
    value = data["value"]
    operation = data["operation"]

    # Проверка прав
    admins = await get_admin_ids(chat_id)
    if callback.from_user.id not in admins:
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    try:
        if operation == "name":
            supabase.table("members") \
                .update({"external_name": value}) \
                .eq("chat_id", chat_id) \
                .eq("user_id", user_id) \
                .execute()

            await callback.message.edit_text(
                f"✨ Имя участника обновлено на <b>{value}</b>",
                parse_mode="HTML"
            )

        elif operation == "role":
            supabase.table("members") \
                .update({"extra_role": value}) \
                .eq("chat_id", chat_id) \
                .eq("user_id", user_id) \
                .execute()

            await callback.message.edit_text(
                f"✨ Роль участника обновлена на <b>{value}</b>",
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"select_user_callback error: {e}")
        await callback.answer("Ошибка сохранения", show_alert=True)
        return

    await callback.answer()

# ========== AUTO-REGISTER ==========

@dp.message(lambda m: m.text and not m.text.startswith("/"))
async def auto_register(msg: types.Message):
    user = msg.from_user
    uid = user.id
    chat_id = msg.chat.id
    now = time.time()

    # --- легкий TTL (анти-спам, 5 сек)
    try:
        res = (
            supabase.table("members")
            .select("user_id")
            .eq("chat_id", chat_id)
            .eq("user_id", uid)
            .maybe_single()
            .execute()
        )
        exists = bool(res.data)
    except:
        exists = False

    if exists:
        last = LAST_UPDATE.get(uid, 0)
        if now - last < UPDATE_TTL:
            return

    LAST_UPDATE[uid] = now

    try:
        res = (
            supabase.table("members")
            .select("*")
            .eq("chat_id", chat_id)
            .eq("user_id", uid)
            .execute()
        )

        if res and isinstance(res.data, list) and len(res.data) > 0:
            row = res.data[0]
        else:
            row = None

    except Exception as e:
        logger.error("Auto-register select error: %s", e)
        row = None


    new_username = user.username or ""
    new_full_name = user.full_name or ""

    # --- если записи НЕТ → добавляем
    if not row:
        await asyncio.to_thread(
            upsert_user,
            chat_id,
            user
        )
        return

    # --- если изменения отсутствуют → не трогаем Supabase
    if (
        row.get("username") == new_username and
        row.get("full_name") == new_full_name
    ):
        return

    # --- изменилось → обновляем только эти 2 поля
    try:
        (
            supabase.table("members")
            .update({
                "username": new_username,
                "full_name": new_full_name
            })
            .eq("chat_id", chat_id)
            .eq("user_id", uid)
            .execute()
        )
    except Exception as e:
        logger.error("Auto-register update error: %s", e)
