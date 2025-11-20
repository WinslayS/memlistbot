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

# ============ DB HELPERS ============

def upsert_user(chat_id: int, user: types.User, external_name: str | None = None):

    # === SKIP Anonymous Admin ===
    if (
        user.username == "GroupAnonymousBot"
        or user.is_bot and user.id == chat_id
        or user.full_name == "Group"  # иногда Telegram отдает так
    ):
        return  # просто не записываем в базу

    payload = {
        "chat_id": chat_id,
        "user_id": user.id,
        "username": user.username or "",
        "full_name": user.full_name or "",
    }

    if external_name is not None:
        payload["external_name"] = external_name

    try:
        return supabase.table("members").upsert(
            payload,
            on_conflict="chat_id, user_id"
        ).execute()
    except Exception as e:
        logger.error("Supabase upsert_user error: %s", e)

def get_members(chat_id: int):
    try:
        res = (
            supabase.table("members")
            .select("*")
            .eq("chat_id", chat_id)
            .order("created_at", desc=False)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error("Supabase get_members error (chat %s): %s", chat_id, e)
        return []

def delete_user(chat_id: int, user_id: int):
    try:
        supabase.table("members") \
            .delete() \
            .eq("chat_id", chat_id) \
            .eq("user_id", user_id) \
            .execute()
        logger.info("Удалён пользователь %s из чата %s", user_id, chat_id)

    except Exception as e:
        logger.error("Supabase delete_user error (chat %s user %s): %s",
                     chat_id, user_id, e)

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
    Формат одной строки:
    1. Андрей, (@Bob123) - Лучший
    """
    full_name = row.get("full_name") or "Без имени"
    username = row.get("username") or ""
    external = row.get("external_name") or ""

    username_part = f" ({make_silent_username(username)})" if username else ""
    external_part = f" — {external}" if external else ""

    if index is not None:
        return f"{index}. {full_name}{username_part}{external_part}"
    return f"{full_name}{username_part}{external_part}"

# ============ CHAT MEMBER EVENTS ============

@dp.chat_member()
async def chat_member_events(event: types.ChatMemberUpdated):
    old = event.old_chat_member.status
    new = event.new_chat_member.status
    user = event.new_chat_member.user
    chat_id = event.chat.id

    # 1) Бота добавили в чат
    if user.id == bot.id and new in ("member", "administrator"):
        await bot.send_message(
            chat_id,
            "🤖 <b>Бот подключён!</b>\n\n"
            "Чтобы всё работало корректно:\n"
            "• дайте мне право <b>«Добавление администраторов»</b>\n"
            "• отключите <b>анонимность администраторов</b>\n"
            "• команды пишите <b>без пробела после слэша</b> — <code>/setname</code>, <code>/export</code>\n\n"
            "После этого все функции будут работать корректно.",
            parse_mode="HTML"
        )
        return

    # 2) Обычный пользователь зашёл в чат
    if old in ("left", "kicked") and new in ("member", "administrator"):
        # игнорируем анонимных / ботов
        if user.username == "GroupAnonymousBot" or user.is_bot:
            return

        await asyncio.to_thread(upsert_user, chat_id, user)
        logger.info("Пользователь %s (%s) добавлен в список чата %s", user.id, user.username, chat_id)

    # 3) Пользователь ушёл или был кикнут
    if new in ("left", "kicked"):
        await asyncio.to_thread(delete_user, chat_id, user.id)
        logger.info("Пользователь %s удалён из списка чата %s", user.id, chat_id)

# ============ COMMANDS ============

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
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
            "/cleanup — очистить список ушедших (админ)\n\n"
            "📖 <b>Обозначения:</b>\n"
            "• <code>[@]</code> — username участника\n"
            "• <code>[имя]</code> — любое текстовое имя\n\n"
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

    lines = ["📋 <b>Список участников:</b>\n"]
    for i, row in enumerate(rows, start=1):
        lines.append(format_member_inline(row, i))

    await msg.answer("\n".join(lines), parse_mode="HTML")

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

# ========== ADMIN: SET NAME FOR ANOTHER USER ==========

@dp.message(Command("setname"))
async def admin_set_name(msg: types.Message):
    if not await admin_check(msg):
        return

    args = msg.text.split(maxsplit=2)
    if len(args) < 3:
        await msg.answer(
            "Формат:\n"
            "/setname @username Имя\n"
            "/setname user_id Имя\n"
            "/setname Имя_пользователя Имя"
        )
        return

    target, new_name = args[1], args[2].strip()

    if not new_name:
        await msg.answer("❌ Имя не может быть пустым.")
        return

    if len(new_name) > 100:
        await msg.answer("❌ Имя слишком длинное. Максимум 100 символов.")
        return

    members = await asyncio.to_thread(get_members, msg.chat.id)

    found_user = None

    # 1️⃣ Если начинается с @ → username
    if target.startswith("@"):
        username = target[1:].lower()
        for m in members:
            if m.get("username", "").lower() == username:
                found_user = m
                break

    # 2️⃣ Если число → user_id
    elif target.isdigit():
        uid = int(target)
        for m in members:
            if m.get("user_id") == uid:
                found_user = m
                break

    # 3️⃣ Иначе → считаем, что это full_name
    else:
        lower_name = target.lower()
        candidates = [m for m in members if m.get("full_name", "").lower() == lower_name]

        if len(candidates) == 1:
            found_user = candidates[0]
        elif len(candidates) > 1:
            await msg.answer("⚠ Найдено несколько пользователей с таким именем — уточните.")
            return

    if not found_user:
        await msg.answer("❌ Пользователь не найден.")
        return

    # обновляем
    uid = found_user["user_id"]

    supabase.table("members") \
        .update({"external_name": new_name}) \
        .eq("chat_id", msg.chat.id) \
        .eq("user_id", uid) \
        .execute()

    await msg.answer(f"✨ Имя участника обновлено на <b>{new_name}</b>", parse_mode="HTML")

    logger.info(
        "Админ %s изменил имя пользователю %s на '%s' в чате %s",
        msg.from_user.id, uid, new_name, msg.chat.id
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

    # Создаём CSV-файл в памяти
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["№", "Full Name", "Username", "External Name"])

    for i, row in enumerate(rows, start=1):
        username = row.get("username") or ""
        writer.writerow([
            i,
            row.get("full_name") or "",
            f"@{username}" if username else "",
            row.get("external_name") or "",
        ])

    # переходим на bytes
    csv_bytes = output.getvalue().encode("utf-8")

    file = BufferedInputFile(
        file=csv_bytes,
        filename=f"members_chat_{msg.chat.id}.csv"
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

        if query in full_name or query in username or query in external:
            results.append(row)

    if not results:
        await msg.answer("❌ Никто не найден.")
        return

    lines = ["🔎 <b>Результаты поиска:</b>\n"]
    for i, row in enumerate(results, start=1):
        lines.append(format_member_inline(row, i))

    await msg.answer("\n".join(lines), parse_mode="HTML")

# ========== CLEANUP (удаление ушедших) ==========

@dp.message(Command("cleanup"))
async def cmd_cleanup(msg: types.Message):
    if not await admin_check(msg):
        return

    rows = await asyncio.to_thread(get_members, msg.chat.id)
    left_users = []

    for row in rows:
        try:
            member = await bot.get_chat_member(msg.chat.id, row["user_id"])
            if member.status in ("left", "kicked"):
                left_users.append(row["user_id"])
        except Exception:
            left_users.append(row["user_id"])

    await asyncio.to_thread(clear_left_users, msg.chat.id, left_users)

    await msg.answer(
        f"🧹 Очистка завершена!\nУдалено: <b>{len(left_users)}</b> пользователей.",
        parse_mode="HTML"
    )

    logger.info(
        "Очистка завершена: удалено %s пользователей в чате %s",
        len(left_users),
        msg.chat.id
    )

# ========== AUTO-REGISTER ==========

@dp.message()
async def auto_register(msg: types.Message):
    if msg.from_user:
        try:
            await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)
            logger.info("Обновление/регистрация пользователя %s (%s) в чате %s",
                        msg.from_user.id, msg.from_user.username, msg.chat.id)
        except Exception as e:
            logger.error("Ошибка Supabase при авто-регистрации: %s", e)

# ============ RUN ============

async def main():
    print("BOT STARTED OK")

    # === Регистрируем команды в Telegram ===
    await bot.set_my_commands([
        types.BotCommand(command="start", description="Запустить бота"),
        types.BotCommand(command="list", description="Показать список участников"),
        types.BotCommand(command="name", description="Установить своё имя"),
        types.BotCommand(command="find", description="Поиск участника"),
        types.BotCommand(command="setname", description="Установить имя другому (админ)"),
        types.BotCommand(command="export", description="Экспорт списка (админ)"),
        types.BotCommand(command="cleanup", description="Очистка списка (админ)"),
    ])

    # Стартуем бота
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
