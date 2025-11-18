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


# ============ UTILS ============

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

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
    1. Vitalii (@w1nslay) — Kvane
    """
    full_name = row.get("full_name") or "Без имени"
    username = row.get("username") or ""
    external = row.get("external_name") or ""

    username_part = f" ({make_silent_username(username)})" if username else ""
    external_part = f" — {external}" if external else ""

    if index is not None:
        return f"{index}. {full_name}{username_part}{external_part}"
    return f"{full_name}{username_part}{external_part}"


# ============ COMMANDS ============

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)

    role = "Админ" if is_admin(msg.from_user.id) else "Участник"

    await msg.answer(
        f"👋 Привет! Ваша роль: <b>{role}</b>\n\n"
        "/list — показать список\n"
        "/name [имя] — установить имя из другого сервиса\n"
        "/find [имя] — найти участника по имени или @\n"
        "/setname [@] [имя] —  установить имя другому участнику (админ)\n"
        "/export — создать файл с участниками (админ)\n"
        "/cleanup — удалить из списка тех, кто вышел из чата (админ)",
        parse_mode="HTML"
    )

@dp.message(Command("list"))
async def cmd_list(msg: types.Message):
    await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)
    rows = await asyncio.to_thread(get_members, msg.chat.id)

    if not rows:
        await msg.answer("Список пуст 🕳️")
        return

    import re

    def esc(text: str) -> str:
        """Экранируем символы для MarkdownV2."""
        return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)

    def safe_username(name: str | None) -> str:
        if not name:
            return ""
        # разрыв, чтобы НЕ было пинга, но выглядело как @username
        broken = "​".join(list(name))  # zero-width chars
        return "@​" + broken

    lines = ["📋 *Список участников:*\n"]

    for i, row in enumerate(rows, start=1):
        full_name = row.get("full_name") or "Без имени"
        username = row.get("username") or ""
        external = row.get("external_name") or ""

        full_name_part = f"`{esc(full_name)}`"

        username_part = f" ({esc(safe_username(username))})" if username else ""

        external_part = f" — {esc(external)}" if external else ""

        lines.append(f"{i}. {full_name_part}{username_part}{external_part}")

    await msg.answer("\n".join(lines), parse_mode="MarkdownV2")

# ========== ADMIN: SET NAME FOR ANOTHER USER ==========

@dp.message(Command("setname"))
async def admin_set_name(msg: types.Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только администратор может менять имена другим участникам.")
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
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только админ может экспортировать список.")
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
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только админ может очищать список.")
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
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
