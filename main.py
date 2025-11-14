import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from supabase import create_client, Client

TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not all([TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    raise Exception("Missing BOT_TOKEN or SUPABASE_URL or SUPABASE_KEY in env variables")

bot = Bot(token=TOKEN)
dp = Dispatcher()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Доступные команды:\n"
        "/join — записаться в список\n"
        "/list — показать список\n"
        "/name Имя — установить имя из другого сервиса"
    )

@dp.message(Command("join"))
async def cmd_join(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    fullname = message.from_user.full_name

    existing = supabase.table("members").select("*").eq("id", user_id).execute()
    if existing.data:
        await message.answer("Ты уже есть в списке 🙂")
        return

    supabase.table("members").insert({
        "id": user_id,
        "username": username,
        "fullname": fullname,
        "external_name": None
    }).execute()

    await message.answer(f"✅ {fullname} добавлен в список!")

@dp.message(Command("name"))
async def cmd_name(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("✍️ Напиши имя после команды. Пример: /name DragonHunter")
        return

    new_name = parts[1]
    user_id = message.from_user.id

    supabase.table("members").update({
        "external_name": new_name
    }).eq("id", user_id).execute()

    await message.answer(f"✅ Имя из другого сервиса установлено: {new_name}")

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    rows = supabase.table("members").select("*").order("created_at", ascending=True).execute().data

    if not rows:
        await message.answer("Список пока пуст 🕳️")
        return

    text = "📋 <b>Список участников:</b>\n\n"
    for i, m in enumerate(rows, start=1):
        username_display = f"@{m['username']}" if m['username'] else m['fullname']
        ext = f" - {m['external_name']}" if m['external_name'] else ""
        text += f"{i}. {username_display}{ext}\n"

    await message.answer(text, parse_mode="HTML")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
