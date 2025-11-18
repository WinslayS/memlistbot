import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from supabase import create_client, Client

# -------- ENV --------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("Missing BOT_TOKEN or SUPABASE_URL or SUPABASE_KEY in env variables")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

OWNER_ID = 8523019691  # твой ID


# ============================================
#  DB HELPERS
# ============================================

def create_or_update_user(msg: types.Message):
    """Создание записи при любом обращении и авто-обновление username/full_name."""
    user = msg.from_user
    chat_id = msg.chat.id

    supabase.table("members").upsert({
        "chat_id": chat_id,
        "user_id": user.id,
        "username": user.username,
        "full_name": user.full_name,
    }).execute()


def set_external_name(user_id, chat_id, external_name):
    return supabase.table("members") \
        .update({"external_name": external_name}) \
        .eq("user_id", user_id) \
        .eq("chat_id", chat_id) \
        .execute()


def get_members(chat_id):
    rows = (
        supabase.table("members")
        .select("*")
        .eq("chat_id", chat_id)
        .order("created_at", desc=False)
        .execute()
        .data
    )
    return rows


def remove_member(chat_id, user_id):
    return supabase.table("members") \
        .delete() \
        .eq("chat_id", chat_id) \
        .eq("user_id", user_id) \
        .execute()


def clear_members(chat_id):
    return supabase.table("members").delete().eq("chat_id", chat_id).execute()


# ============================================
#  MIDDLEWARE: auto-update + auto-create
# ============================================

@dp.message()
async def auto_register_and_update(msg: types.Message, handler):
    """Выполняется ПЕРЕД любой командой — создаёт пользователя и обновляет данные."""
    create_or_update_user(msg)
    return await handler(msg)


# ============================================
#  COMMANDS
# ============================================

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    await msg.answer(
        "👋 Привет! Доступные команды:\n"
        "/join — записаться в список\n"
        "/list — показать список\n"
        "/name ИМЯ — установить имя из другого сервиса\n"
        "/remove — удалить себя\n"
        "/clear — очистить список (админ)"
    )


@dp.message(Command("join"))
async def cmd_join(msg: types.Message):
    user = msg.from_user

    await msg.answer(f"✅ {user.full_name} добавлен в список!")


@dp.message(Command("list"))
async def cmd_list(msg: types.Message):
    chat_id = msg.chat.id
    rows = get_members(chat_id)

    if not rows:
        await msg.answer("Список пуст 🕳️")
        return

    text = "📋 <b>Список участников:</b>\n\n"

    for i, row in enumerate(rows, start=1):
        uname = f"@{row['username']}" if row["username"] else row["full_name"]
        extr = f" — {row['external_name']}" if row.get("external_name") else ""
        text += f"{i}. {uname}{extr}\n"

    await msg.answer(text, parse_mode="HTML")


@dp.message(Command("name"))
async def cmd_name(msg: types.Message):
    chat_id = msg.chat.id
    user = msg.from_user
    args = msg.text.split(" ", 1)

    if len(args) < 2:
        await msg.answer("✏️ Напиши имя после команды. Пример: /name DragonHunter")
        return

    name = args[1].strip()

    set_external_name(user.id, chat_id, name)

    await msg.answer(f"✅ Имя из другого сервиса установлено: {name}")


@dp.message(Command("remove"))
async def cmd_remove(msg: types.Message):
    chat_id = msg.chat.id
    user = msg.from_user

    remove_member(chat_id, user.id)

    await msg.answer("🗑 Ты удалён из списка!")


@dp.message(Command("clear"))
async def cmd_clear(msg: types.Message):
    if msg.from_user.id != OWNER_ID:
        await msg.answer("⛔ Только владелец бота может очистить список!")
        return

    chat_id = msg.chat.id
    clear_members(chat_id)

    await msg.answer("🧹 Список полностью очищен!")


# ============================================
#  RUN
# ============================================

async def main():
    print("BOT STARTED OK")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
