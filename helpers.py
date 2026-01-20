import time
import asyncio
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from logger import logger
from db import get_members, supabase

# ============ GLOBAL CACHES / CONSTS ============

# user_id -> last update timestamp
LAST_UPDATE: dict[int, float] = {}
UPDATE_TTL = 10  # секунды

# chat_id -> (timestamp, set(admin_ids))
ADMIN_CACHE: dict[int, tuple[float, set[int]]] = {}
ADMIN_CACHE_TTL = 10.0  # секунды

# task_id -> {chat_id, user_id, value, operation}
PENDING_ACTIONS: dict[str, dict] = {}

# welcome anti-spam per chat
WELCOME_SENT: dict[int, float] = {}
WELCOME_TTL = 3600

ZERO_WIDTH_SPACE = "\u200B"  # невидимый символ

# ========== HELPER: SEND LONG MESSAGE ==========

async def send_long_message(bot, msg: types.Message, header: str, text: str):
    chat_id = msg.chat.id
    thread_id = msg.message_thread_id

    MAX_LEN = 4096

    parts = []
    while len(text) > MAX_LEN:
        split_pos = text.rfind("\n", 0, MAX_LEN)
        if split_pos == -1:
            split_pos = MAX_LEN
        parts.append(text[:split_pos])
        text = text[split_pos:].lstrip()
    parts.append(text)

    total = len(parts)

    for i, part in enumerate(parts, start=1):
        title = f"{header} ({i}/{total})"
        await bot.send_message(
            chat_id,
            f"<b>{title}</b>\n\n{part}",
            parse_mode="HTML",
            message_thread_id=thread_id
        )

# ============ ADMIN CHECKER (с кэшем) ============

async def get_admin_ids(bot, chat_id: int) -> set[int]:
    """Возвращает множество ID админов с кэшем на несколько секунд."""
    now = time.time()
    cached = ADMIN_CACHE.get(chat_id)

    if cached and now - cached[0] < ADMIN_CACHE_TTL:
        return cached[1]

    try:
        admins = await bot.get_chat_administrators(chat_id)
        admin_ids = {a.user.id for a in admins}
        ADMIN_CACHE[chat_id] = (now, admin_ids)
        return admin_ids
    except Exception as e:
        logger.error("Ошибка получения админов для чата %s: %s", chat_id, e)
        return set()


async def is_user_admin(bot, msg: types.Message) -> bool:
    """Проверка: пользователь — администратор чата?"""
    admin_ids = await get_admin_ids(bot, msg.chat.id)
    return msg.from_user.id in admin_ids


async def is_bot_admin(bot, msg: types.Message) -> bool:
    """Проверка: бот — администратор в чате?"""
    admin_ids = await get_admin_ids(bot, msg.chat.id)
    return bot.id in admin_ids


async def admin_check(bot, msg: types.Message) -> bool:
    """
    Общая проверка для админ-команд.
    True — можно выполнять команду.
    False — надо остановиться.
    """
    if msg.chat.type == "private":
        await msg.answer("❌ Эта команда работает только в групповых чатах.")
        return False

    admin_ids = await get_admin_ids(bot, msg.chat.id)

    if msg.from_user.id not in admin_ids:
        await msg.answer("⛔ Эта команда доступна только администраторам.")
        return False

    if bot.id not in admin_ids:
        await msg.answer(
            "⚠️ Я не являюсь администратором, поэтому не могу выполнить команду.\n\n"
            "Пожалуйста, выдайте мне право <b>«Добавление администраторов»</b>.",
            parse_mode="HTML"
        )
        return False

    return True

# ============ FORMAT HELPERS ============

def make_silent_username(username: str) -> str:
    if not username:
        return ""
    return f"@{ZERO_WIDTH_SPACE}{username}"

def format_member_inline(row: dict, index: int | None = None) -> str:
    full_name = row.get("full_name") or "Без имени"
    username = row.get("username") or ""
    external = row.get("external_name") or ""
    role = row.get("extra_role") or ""
    role_part = f" — <i>{role}</i>" if role else ""

    username_part = f" ({make_silent_username(username)})" if username else ""
    external_part = f" — {external}" if external else ""

    if index is not None:
        return f"{index}. {full_name}{username_part}{external_part}{role_part}"
    return f"{full_name}{username_part}{external_part}{role_part}"

def format_member_txt(row: dict, index: int | None = None) -> str:
    full_name = row.get("full_name") or "Без имени"
    username = row.get("username") or ""
    external = row.get("external_name") or ""
    role = row.get("extra_role") or ""

    username_part = f" (@{username})" if username else ""
    external_part = f" — {external}" if external else ""
    role_part = f" — {role}" if role else ""

    if index is not None:
        return f"{index}. {full_name}{username_part}{external_part}{role_part}"
    return f"{full_name}{username_part}{external_part}{role_part}"

# ============ USER BY TARGET ============

async def find_user_by_target(chat_id: int, target: str):
    """
    Улучшенный поиск:
    - @username
    - user_id
    - точное совпадение full_name / external_name
    - частичный поиск (как /find)
    """
    rows = await asyncio.to_thread(get_members, chat_id)
    target = target.strip().lower()

    # 1) @username
    if target.startswith("@"):
        uname = target[1:]
        matches = [m for m in rows if (m.get("username") or "").lower() == uname]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return "MULTIPLE"
        return None

    # 2) user_id
    if target.isdigit():
        uid = int(target)
        return next((m for m in rows if m.get("user_id") == uid), None)

    # 3) Полное совпадение full_name/external_name
    exact = [
        m for m in rows
        if (m.get("full_name") or "").lower() == target
        or (m.get("external_name") or "").lower() == target
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return "MULTIPLE"

    # 4) Частичный поиск
    partial = [
        m for m in rows
        if target in (m.get("full_name") or "").lower()
        or target in (m.get("external_name") or "").lower()
        or target in (m.get("username") or "").lower()
    ]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        return "MULTIPLE"

    return None

# ============ MULTI TARGET ============

async def show_user_selection(msg: types.Message, matches: list, operation: str, value: str):
    kb = InlineKeyboardBuilder()
    text_lines = ["⚠ Найдено несколько участников:\n"]

    for m in matches:
        uid = m["user_id"]
        full = m.get("full_name") or "Без имени"
        ext = m.get("external_name") or ""
        uname = m.get("username") or ""

        display = full
        if ext:
            display += f" — {ext}"
        if uname:
            display += f" (@{uname})"

        text_lines.append(f"• {display}")

        task_id = f"{msg.chat.id}_{uid}_{operation}_{int(time.time())}"

        PENDING_ACTIONS[task_id] = {
            "chat_id": msg.chat.id,
            "user_id": uid,
            "value": value,
            "operation": operation
        }

        kb.button(
            text=full[:20],
            callback_data=f"select_user:{task_id}"
        )

    kb.adjust(2)

    await msg.answer(
        "\n".join(text_lines) + "\n\nВыберите нужного:",
        reply_markup=kb.as_markup()
    )

def get_target_user_from_reply(msg: types.Message):
    reply = msg.reply_to_message
    if not reply:
        return None

    if reply.new_chat_members:
        if len(reply.new_chat_members) == 1:
            return reply.new_chat_members[0]
        return None

    if reply.from_user:
        if reply.from_user.is_bot:
            return None
        return reply.from_user

    return None

async def delete_command_later(msg: types.Message, delay: int = 5):
    """
    Пытается удалить сообщение с командой через delay секунд.
    Ошибки (нет прав и т.п.) подавляются.
    """
    try:
        await asyncio.sleep(delay)
        await msg.delete()
    except Exception as e:
        logger.debug("Failed to delete command message: %s", e)

def extract_users_from_message(msg: types.Message) -> list[types.User]:
    """
    Извлекает пользователей из сообщения:
    - text_mention (выбор из списка Telegram)
    - mention (@username) — ТОЛЬКО если пользователь есть в БД members
    """
    users: dict[int, types.User] = {}

    if not msg.entities:
        return []

    for entity in msg.entities:
        # 1️⃣ Пользователь выбран из списка Telegram (есть user_id)
        if entity.type == "text_mention" and entity.user:
            users[entity.user.id] = entity.user
            continue

        # 2️⃣ Пользователь указан как @username
        if entity.type == "mention":
            raw = msg.text[entity.offset : entity.offset + entity.length]
            
            username = (
                raw
                .lstrip("@")
                .strip()
                .lower()
            )

            # ищем пользователя в БД
            res = (
                supabase
                .table("members")
                .select("user_id, full_name, username, external_name")
                .eq("chat_id", msg.chat.id)
                .ilike("username", username)
                .limit(1)
                .execute()
            )

            if not res.data:
                continue  # пользователя нет в БД — игнорируем

            row = res.data[0]

            user = types.User(
                id=row["user_id"],
                is_bot=False,
                first_name=row.get("full_name") or row.get("external_name") or username,
                username=row.get("username"),
            )

            users[user.id] = user

    return list(users.values())

