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
UPDATE_TTL = 60

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
            "• команды пишите <b>без пробела после слэша</b> — <code>/setname</code>, <code>/export</code>\n\n"
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
                    "/cleanup — очистить список ушедших (админ)\n\n"
                    "📖 <b>Как добавить участника:</b>\n"
                    "• Если есть username (@):\n"
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
            "/cleanup — очистить список ушедших (админ)\n\n"
            "📖 <b>Как добавить участника в список:</b>\n"
            "• Если у участника <b>есть username (@)</b> — используйте:\n"
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

    # ===============================
    #     СПОСОБ №1 — ЧЕРЕЗ REPLY
    # ===============================
    if msg.reply_to_message:
        target_user = msg.reply_to_message.from_user

        args = msg.text.split(maxsplit=1)
        if len(args) < 2:
            await msg.answer("Напишите новое имя. Пример:\n/setname Иван")
            return

        new_name = args[1].strip()

        # Добавляем или обновляем пользователя
        supabase.table("members").upsert({
            "chat_id": msg.chat.id,
            "user_id": target_user.id,
            "username": target_user.username or "",
            "full_name": target_user.full_name or "",
            "external_name": new_name
        }, on_conflict="chat_id,user_id").execute()

        await msg.answer(
            f"✨ Имя участника <b>{target_user.full_name}</b> обновлено на <b>{new_name}</b>",
            parse_mode="HTML"
        )
        return

    # ===============================
    #     СПОСОБ №2 — @username / id / имя
    # ===============================

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

    target, new_name = args[1].strip(), args[2].strip()

    members = await asyncio.to_thread(get_members, msg.chat.id)

    found_user = None

    # 1️⃣ username
    if target.startswith("@"):
        uname = target[1:].lower()
        found_user = next((m for m in members if (m.get("username") or "").lower() == uname), None)

    # 2️⃣ user_id
    elif target.isdigit():
        uid = int(target)
        found_user = next((m for m in members if m.get("user_id") == uid), None)

    # 3️⃣ full_name
    else:
        name_lower = target.lower()
        candidates = [m for m in members if (m.get("full_name") or "").lower() == name_lower]

        if len(candidates) == 1:
            found_user = candidates[0]
        elif len(candidates) > 1:
            await msg.answer("⚠ Найдено несколько участников с таким именем — уточните.")
            return

    if not found_user:
        await msg.answer("❌ Пользователь не найден в базе. Используйте reply на его сообщение.")
        return

    uid = found_user["user_id"]

    supabase.table("members").update({"external_name": new_name}).eq(
        "chat_id", msg.chat.id
    ).eq("user_id", uid).execute()

    await msg.answer(
        f"✨ Имя участника обновлено на <b>{new_name}</b>",
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

    output = io.StringIO()

    # Первая строка как в Telegram
    output.write("📋 Список участников:\n\n")

    # Формируем строки в ТГ-формате
    for i, row in enumerate(rows, start=1):
        line = format_member_inline(row, i)   # ← та же функция!
        output.write(line + "\n")

    csv_bytes = output.getvalue().encode("utf-8")

    file = BufferedInputFile(
        file=csv_bytes,
        filename=f"members_chat_{msg.chat.id}.txt"   # лучше TXT, не CSV
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

# ========== AUTO-REGISTER ==========

@dp.message()
async def auto_register(msg: types.Message):
    user = msg.from_user
    uid = user.id
    now = time.time()

    # Если обновляли < 60 сек назад — пропускаем
    last = LAST_UPDATE.get(uid, 0)
    if now - last < UPDATE_TTL:
        return

    # Обновляем запись
    try:
        await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)
        LAST_UPDATE[uid] = now  # фиксируем время последнего обновления
        logger.info("Обновление пользователя %s в чате %s", uid, msg.chat.id)
    except Exception as e:
        logger.error("Ошибка Supabase при авто-регистрации: %s", e)

# ============ RUN ============

async def main():
    print("BOT STARTED OK")

    # === Регистрируем команды в Telegram ===
    await bot.set_my_commands([
        types.BotCommand(command="help", description="Помощь / команды"),
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
