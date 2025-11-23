import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from supabase import create_client, Client

# ============ LOGGING ============

import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger(__name__)

class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[37m",      # серый
        logging.INFO: "\033[36m",       # голубой
        logging.WARNING: "\033[33m",    # жёлтый
        logging.ERROR: "\033[31m",      # красный
        logging.CRITICAL: "\033[91m",   # ярко-красный
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, self.RESET)
        message = super().format(record)
        return f"{color}{message}{self.RESET}"

handler = logging.StreamHandler()
handler.setFormatter(ColorFormatter("[%(levelname)s] %(message)s"))

logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)

# ============ ENV ============

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMINS = os.getenv("ADMINS", "")

ADMIN_IDS = {int(x) for x in ADMINS.split(",") if x.strip().isdigit()}

if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing BOT_TOKEN or SUPABASE_URL or SUPABASE_KEY in env variables")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ================= CACHING FOR AUTO-REGISTER =================

# user_id -> last update timestamp
LAST_UPDATE: dict[int, float] = {}

# Время обновления (рекомендуется 60 секунд)
UPDATE_TTL = 10

# ============ DB HELPERS ============

def upsert_user(chat_id: int, user: types.User, external_name=None, extra_role=None):
    if user.username == "GroupAnonymousBot" or (user.is_bot and user.id != chat_id):
        return

    try:
        # === 1. Пытаемся получить запись ===
        res = (
            supabase.table("members")
            .select("*")
            .eq("chat_id", chat_id)
            .eq("user_id", user.id)
            .maybe_single()
            .execute()
        )

        row = res.data

        # === 2. Если НЕТ записи — создаём ===
        if not row:
            payload = {
                "chat_id": chat_id,
                "user_id": user.id,
                "username": user.username or "",
                "full_name": user.full_name or "",
                "external_name": external_name or "",
                "extra_role": extra_role or "",
            }

            supabase.table("members").insert(payload).execute()
            return

        # === 3. Если запись есть — обновляем только изменившиеся поля ===
        update_data = {}
        new_username = user.username or ""
        new_full_name = user.full_name or ""

        if row.get("username") != new_username:
            update_data["username"] = new_username

        if row.get("full_name") != new_full_name:
            update_data["full_name"] = new_full_name

        if external_name is not None:
            update_data["external_name"] = external_name

        if extra_role is not None:
            update_data["extra_role"] = extra_role

        if update_data:
            (
                supabase.table("members")
                .update(update_data)
                .eq("chat_id", chat_id)
                .eq("user_id", user.id)
                .execute()
            )

    except Exception as e:
        logger.error("Supabase upsert_user FIXED error: %s", e)

def get_members(chat_id: int):
    try:
        res = (
            supabase.table("members")
            .select("*")
            .eq("chat_id", chat_id)
            .order("id")
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error("Supabase get_members error: %s", e)
        return []

def delete_user(chat_id: int, user_id: int):
    try:
        (
            supabase.table("members")
            .delete()
            .eq("chat_id", chat_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.error("delete_user error: %s", e)

def clear_left_users(chat_id: int, left_user_ids: list[int]):
    for uid in left_user_ids:
        try:
            supabase.table("members") \
                .delete() \
                .eq("chat_id", chat_id) \
                .eq("user_id", uid) \
                .execute()

            logger.info("Удалён из базы ушедший пользователь %s из чата %s",
                        uid, chat_id)

        except Exception as e:
            logger.error("Supabase clear_left_users error (chat %s user %s): %s",
                         chat_id, uid, e)

# ========== HELPER: SEND LONG MESSAGE ==========

async def send_long_message(msg: types.Message, header: str, text: str):
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

# chat_id -> (timestamp, set(admin_ids))
ADMIN_CACHE: dict[int, tuple[float, set[int]]] = {}
ADMIN_CACHE_TTL = 10.0  # секунды


async def get_admin_ids(chat_id: int) -> set[int]:
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


async def is_user_admin(msg: types.Message) -> bool:
    """Проверка: пользователь — администратор чата?"""
    admin_ids = await get_admin_ids(msg.chat.id)
    return msg.from_user.id in admin_ids


async def is_bot_admin(msg: types.Message) -> bool:
    """Проверка: бот — администратор в чате?"""
    admin_ids = await get_admin_ids(msg.chat.id)
    return bot.id in admin_ids


async def admin_check(msg: types.Message) -> bool:
    """
    Общая проверка для админ-команд.
    True — можно выполнять команду.
    False — надо остановиться.
    """

    # 1) Команда только для групп
    if msg.chat.type == "private":
        await msg.answer("❌ Эта команда работает только в групповых чатах.")
        return False

    admin_ids = await get_admin_ids(msg.chat.id)

    # 2) Пользователь не админ
    if msg.from_user.id not in admin_ids:
        await msg.answer("⛔ Эта команда доступна только администраторам.")
        return False

    # 3) Бот не админ
    if bot.id not in admin_ids:
        await msg.answer(
            "⚠️ Я не являюсь администратором, поэтому не могу выполнить команду.\n\n"
            "Пожалуйста, выдайте мне право <b>«Добавление администраторов»</b>.",
            parse_mode="HTML"
        )
        return False

    return True

# ============ FORMAT HELPERS ============

ZERO_WIDTH_SPACE = "\u200B"  # невидимый символ

def make_silent_username(username: str) -> str:
    if not username:
        return ""
    # @ + zero-width-space + username
    return f"@{ZERO_WIDTH_SPACE}{username}"

def format_member_inline(row: dict, index: int | None = None) -> str:
    """
    Формат одной строки для Telegram (HTML):
    1. Андрей (@andre) — проверка — <i>глава смены</i>
    """
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
    """Формат строки для TXT экспорта (БЕЗ HTML-тегов)."""
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
        matches = [
            m for m in rows
            if (m.get("username") or "").lower() == uname
        ]
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

    # 4) Частичный поиск (как /find)
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

from aiogram.utils.keyboard import InlineKeyboardBuilder

PENDING_ACTIONS = {}  # task_id -> data

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

        # создаём уникальный task_id
        task_id = f"{msg.chat.id}_{uid}_{operation}_{int(time.time())}"

        # сохраняем данные
        PENDING_ACTIONS[task_id] = {
            "chat_id": msg.chat.id,
            "user_id": uid,
            "value": value,
            "operation": operation
        }

        kb.button(
            text=full[:20],  # текст на кнопке
            callback_data=f"select_user:{task_id}"
        )

    kb.adjust(2)

    await msg.answer(
        "\n".join(text_lines) + "\n\nВыберите нужного:",
        reply_markup=kb.as_markup()
    )

# ============ CHAT MEMBER EVENTS ============

WELCOME_SENT = set()

@dp.chat_member()
async def chat_member_events(event: types.ChatMemberUpdated):
    old = event.old_chat_member.status
    new = event.new_chat_member.status
    user = event.new_chat_member.user
    chat_id = event.chat.id

    # 1) Бота добавили в чат
    if user.id == bot.id and new in ("member", "administrator"):

        # Сообщение №1 — стандартное, как раньше
        await bot.send_message(
            chat_id,
            "🤖 <b>Бот подключён!</b>\n\n"
            "Чтобы всё работало корректно:\n"
            "• дайте мне право <b>«Добавление администраторов»</b>\n"
            "• отключите <b>анонимность администраторов</b>\n"
            "• команды пишите <b>без пробела после слэша</b> — <code>/setname</code>, <code>/export</code>\n"
            "• имейте ввиду, что в бот поступают данные с момента добавления его в группу\n\n"
            "После этого все функции будут работать корректно.",
            parse_mode="HTML"
        )

        # Сообщение №2 — HELP, только 1 раз для этого чата
        if chat_id not in WELCOME_SENT:
            WELCOME_SENT.add(chat_id)

            await bot.send_message(
                chat_id,
                (
                    "👋 <b>Привет! Вот краткая справка по боту:</b>\n\n"
                    "📌 <b>Команды:</b>\n"
                    "/list — показать список участников\n"
                    "/name [имя] — установить своё имя\n"
                    "/find [имя/@] — поиск участника\n"
                    "/setname [@] [имя] — назначить имя другому (админ)\n"
                    "/export — экспорт списка (админ)\n"
                    "/cleanup — очистить список ушедших (админ)\n"
                    "/add [роль] — установить себе роль (участник)\n"
                    "/addrole [@] [роль] — назначить роль другому участнику (админ)\n\n"
                    "📖 <b>Как добавить участника:</b>\n"
                    "• Если есть username (@) в базе данных (автоматически при заходе):\n"
                    "  <code>/setname @username Имя</code>\n\n"
                    "• Если username нет:\n"
                    "  1) участник пишет любое сообщение в чат\n"
                    "  2) админ отвечает на это сообщение:\n"
                    "     <code>/setname Имя</code>\n\n"
                    "• Если участник хочет сам установить имя:\n"
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

        return  # ⚠️ Оставляем! Чтобы старая логика не ломалась

    # 2) Обычный пользователь зашёл в чат
    if old in ("left", "kicked") and new in ("member", "administrator"):
        # игнорируем анонимных / ботов
        if user.username == "GroupAnonymousBot" or user.is_bot:
            return

        await asyncio.to_thread(upsert_user, chat_id, user)
        logger.info(
            "Пользователь %s (%s) добавлен в список чата %s",
            user.id, user.username, chat_id
        )

    # 3) Пользователь ушёл или был кикнут
    if new in ("left", "kicked"):
        await asyncio.to_thread(delete_user, chat_id, user.id)
        logger.info(
            "Пользователь %s удалён из списка чата %s",
            user.id, chat_id
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
    await send_long_message(msg, "📋 Список участников", full_text)

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

# ========== ADMIN: SET NAME FOR ANOTHER USER ==========

@dp.message(Command("setname"))
async def admin_set_name(msg: types.Message):
    if not await admin_check(msg):
        return

    # ---------- РЕЖИМ ЧЕРЕЗ REPLY ----------
    if msg.reply_to_message:
        target_user = msg.reply_to_message.from_user

        args = msg.text.split(maxsplit=1)
        if len(args) < 2:
            await msg.answer("Напишите новое имя. Пример:\n/setname Иван")
            return

        new_name = args[1].strip()

        if new_name.startswith("@"):
            parts = new_name.split(maxsplit=1)
            if len(parts) == 2:
                new_name = parts[1].strip()

        if not new_name:
            await msg.answer("❌ Имя не может быть пустым.")
            return

        # ---- ВОТ ТУТ update ----
        try:
            (
                supabase.table("members")
                .update({"external_name": new_name})
                .eq("chat_id", msg.chat.id)
                .eq("user_id", target_user.id)
                .execute()
            )
        except Exception as e:
            logger.error("Supabase setname(reply) UPDATE error: %s", e)
            await msg.answer("⚠ Ошибка при сохранении.")
            return

        await msg.answer(
            f"✨ Имя участника <b>{target_user.full_name}</b> обновлено на <b>{new_name}</b>",
            parse_mode="HTML"
        )
        return

    # ---------- РЕЖИМ ЧЕРЕЗ ТЕКСТ ----------
    args = msg.text.split(maxsplit=2)
    if len(args) < 3:
        await msg.answer(
            "Форматы:\n"
            "/setname @username Имя\n"
            "/setname user_id Имя\n"
            "/setname ПолноеИмя Имя\n"
            "ИЛИ ответом на сообщение:\n"
            "/setname НовоеИмя"
        )
        return

    target = args[1].strip()
    new_name = args[2].strip()

    if new_name.startswith("@"):
        parts = new_name.split(maxsplit=1)
        if len(parts) == 2:
            new_name = parts[1].strip()

    if not new_name:
        await msg.answer("❌ Имя не может быть пустым.")
        return

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

        await show_user_selection(msg, filtered, "name", new_name)
        return


    if not found_user:
        await msg.answer("❌ Участник не найден.")
        return

    uid = found_user["user_id"]

    # --- ВОТ ТУТ update ---
    try:
        (
            supabase.table("members")
            .update({"external_name": new_name})
            .eq("chat_id", msg.chat.id)
            .eq("user_id", uid)
            .execute()
        )
    except Exception as e:
        logger.error("Supabase setname(update) error: %s", e)
        await msg.answer("⚠ Ошибка при обновлении.")
        return

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
    if msg.reply_to_message:
        target = msg.reply_to_message.from_user
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

    await send_long_message(msg, "🔎 Результаты поиска", full_text)

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

@dp.message()
async def auto_register(msg: types.Message):
    user = msg.from_user
    uid = user.id
    chat_id = msg.chat.id
    now = time.time()

    # --- легкий TTL (анти-спам, 5 сек)
    last = LAST_UPDATE.get(uid, 0)
    if now - last < UPDATE_TTL:
        return

    LAST_UPDATE[uid] = now

    # --- достаём текущие данные
    try:
        res = (
            supabase.table("members")
            .select("*")
            .eq("chat_id", chat_id)
            .eq("user_id", uid)
            .single()
            .execute()
        )
        row = res.data
    except:
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

# ============ RUN ============

async def main():
    print("BOT STARTED OK")

    # === Регистрируем команды в Telegram ===
    await bot.set_my_commands([
        types.BotCommand(command="help", description="Помощь / команды"),
        types.BotCommand(command="list", description="Показать список участников"),
        types.BotCommand(command="name", description="Установить своё имя"),
        types.BotCommand(command="add", description="Установить себе роль"),
        types.BotCommand(command="find", description="Поиск участника"),
        types.BotCommand(command="setname", description="Установить имя другому (админ)"),
        types.BotCommand(command="addrole", description="Назначить роль участнику (админ)"),
        types.BotCommand(command="export", description="Экспорт списка (админ)"),
        types.BotCommand(command="cleanup", description="Очистка списка (админ)"),
    ])
    
    # Стартуем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

