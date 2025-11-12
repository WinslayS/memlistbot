import json
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message

TOKEN = "ТОКЕН_ОТ_BOTFATHER"
DATA_FILE = "members.json"

bot = Bot(token=TOKEN)
dp = Dispatcher()

def load_members():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_members(members):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(members, f, ensure_ascii=False, indent=2)

@dp.message(Command("start"))
async def start(message: Message):
    await message.answer("👋 Привет! Напиши /join чтобы записаться, или /list чтобы увидеть список.")

@dp.message(Command("join"))
async def join(message: Message):
    members = load_members()
    user = {
        "id": message.from_user.id,
        "name": message.from_user.full_name,
        "username": message.from_user.username
    }

    if not any(m["id"] == user["id"] for m in members):
        members.append(user)
        save_members(members)
        await message.answer(f"✅ {message.from_user.full_name} добавлен в список!")
    else:
        await message.answer("Ты уже есть в списке 🙂")

@dp.message(Command("list"))
async def show_list(message: Message):
    members = load_members()
    if not members:
        await message.answer("Список пока пуст 🕳️")
        return

    text = "📋 <b>Список участников:</b>\n\n"
    for i, m in enumerate(members, start=1):
        if m.get("username"):
            text += f"{i}. @{m['username']}\n"
        else:
            text += f"{i}. {m['name']} (без @)\n"

    await message.answer(text, parse_mode="HTML")

async def main():
    print("🚀 Бот запущен и слушает сообщения...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
