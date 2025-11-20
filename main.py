import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from supabase import create_client, Client

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

    return supabase.table("members").upsert(
        payload,
        on_conflict="chat_id, user_id"
    ).execute()

def get_members(chat_id: int):
    res = (
        supabase.table("members")
        .select("*")
        .eq("chat_id", chat_id)
        .order("created_at", desc=False)
        .execute()
    )
    return res.data or []

def delete_user(chat_id: int, user_id: int):
    return (
        supabase.table("members")
        .delete()
        .eq("chat_id", chat_id)
        .eq("user_id", user_id)
        .execute()
    )

def clear_left_users(chat_id: int, left_user_ids: list[int]):
    for uid in left_user_ids:
        supabase.table("members").delete().eq("chat_id", chat_id).eq("user_id", uid).execute()

# ============ ADMIN CHECKER ============

async def is_user_admin(msg: types.Message) -> bool:
    """Проверка: пользователь — администратор чата?"""
    try:
        admins = await msg.chat.get_administrators()
        admin_ids = [a.user.id for a in admins]
        return msg.from_user.id in admin_ids
    except Exception as e:
        print("ADMIN USER CHECK ERROR:", e)
        return False


async def is_bot_admin(msg: types.Message) -> bool:
    """Проверка: бот — администратор чата?"""
    try:
        admins = await msg.chat.get_administrators()
        admin_ids = [a.user.id for a in admins]
        return msg.bot.id in admin_ids
    except Exception as e:
        print("ADMIN BOT CHECK ERROR:", e)
        return False

async def admin_check(msg: types.Message) -> bool:
    """
    Возвращает True — если всё в порядке.
    Возвращает False — если команду нужно остановить.
    """

    user_admin = await is_user_admin(msg)
    bot_admin = await is_bot_admin(msg)

    # Пользователь не админ
    if not user_admin:
        await msg.answer("⛔ Эта команда доступна только администраторам.")
        return False

    # Пользователь админ, но бот нет
    if not bot_admin:
        await msg.answer(
            "⚠️ Я не являюсь администратором, поэтому не могу выполнить команду.\n\n"
            "Пожалуйста, выдайте мне право <b>«Добавление администраторов»</b>.",
            parse_mode="HTML"
        )
        return False

    # Всё хорошо — можно выполнять
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

# ============ FIRST MESSAGE ============

@dp.chat_member()
async def on_bot_added(event: types.ChatMemberUpdated):
    if event.new_chat_member.user.id == bot.id and event.new_chat_member.status == "member":
        await bot.send_message(
            event.chat.id,
            "🤖 <b>Бот подключён!</b>\n\n"
            "Чтобы всё работало корректно:\n"
            "• дайте мне право <b>«Добавление администраторов»</b>\n"
            "• отключите <b>анонимность администраторов</b>\n"
            "• команды пишите <b>без пробела после слэша</b> — <code>/setname</code>, <code>/export</code>\n\n"
            "После этого все функции будут работать корректно.",
            parse_mode="HTML"
        )

# ============ AUTO ADD NEW CHAT MEMBERS ============

@dp.chat_member()
async def on_user_join(event: types.ChatMemberUpdated):
    old = event.old_chat_member.status
    new = event.new_chat_member.status

    # Новичок вошёл в чат
    if old in ("left", "kicked") and new in ("member", "administrator"):
        user = event.new_chat_member.user

        # игнорируем анонимных админов и ботов
        if user.username == "GroupAnonymousBot" or user.is_bot:
            return

        # добавляем человека в базу
        await asyncio.to_thread(
            upsert_user,
            event.chat.id,
            user
        )

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
    # Разбиваем текст: "/name Kvane"
    args = msg.text.split(maxsplit=1)

    # Если аргумента нет
    if len(args) < 2:
        await msg.answer("✏️ Напиши имя после команды. Пример: /name Kvane")
        return

    external_name = args[1].strip()

    # Обновляем / создаём пользователя с external_name
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
        await msg.answer("Формат: /setname @username Имя")
        return

    target, new_name = args[1], args[2].strip()

    if target.startswith("@"):
        target_username = target[1:]
        condition = ("username", target_username)
    else:
        try:
            target_uid = int(target)
            condition = ("user_id", target_uid)
        except:
            await msg.answer("❌ Укажите @username или user_id")
            return

    column, value = condition

    result = (
        supabase.table("members")
        .select("*")
        .eq("chat_id", msg.chat.id)
        .eq(column, value)
        .execute()
    )

    rows = result.data or []
    if not rows:
        await msg.answer("❌ Пользователь не найден в базе.")
        return

    uid = rows[0]["user_id"]

    supabase.table("members").update({"external_name": new_name}).eq("chat_id", msg.chat.id).eq("user_id", uid).execute()

    await msg.answer(f"✨ Имя участника обновлено на <b>{new_name}</b>", parse_mode="HTML")

# ========== ADMIN EXPORT CSV ==========

import csv
import io
from aiogram.types import InputFile

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

    output.seek(0)
    file = InputFile(path_or_bytesio=output, filename=f"members_chat_{msg.chat.id}.csv")

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

    await msg.answer(f"🧹 Очистка завершена!\nУдалено: <b>{len(left_users)}</b> пользователей.", parse_mode="HTML")

# ========== AUTO-REGISTER ==========

@dp.message()
async def auto_register(msg: types.Message):
    if msg.from_user:
        try:
            await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)
        except Exception as e:
            print("Supabase error:", e)

# ========== HANDLE USER LEAVING CHAT ==========

@dp.chat_member()
async def chat_member_update(event: types.ChatMemberUpdated):
    old = event.old_chat_member.status
    new = event.new_chat_member.status

    # Если пользователь ушёл или был кикнут
    if new in ("left", "kicked"):
        await asyncio.to_thread(delete_user, event.chat.id, event.from_user.id)

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
