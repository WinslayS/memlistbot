import json
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message

TOKEN = "8559168291:AAHTWpAoSD1rtKHkCXWcIvcvSLPCBJpD0CM"
DATA_FILE = "members.json"

bot = Bot(token=TOKEN)
dp = Dispatcher()

def load_members():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return []
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_members(members):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(members, f, ensure_ascii=False, indent=2)
    print("✅ Сохранено:", members)

@dp.message(Command("start"))
async def start(message: Message):
    await message.answer("👋 Привет! Напиши /join чтобы записаться, /list чтобы увидеть список, или /name <твоё имя> чтобы указать своё имя.")

@dp.message(Command("join"))
async def join(message: Message):
    members = load_members()
    user = {
        "id": message.from_user.id,
        "username": message.from_user.username,
        "custom_name": message.from_user.full_name
    }

    if not any(m["id"] == user["id"] for m in members):
        members.append(user)
        save_members(members)
        await message.answer(f"✅ {message.from_user.full_name} добавлен в список!")
    else:
        await message.answer("Ты уже есть в списке 🙂")

@dp.message(Command("name"))
async def set_name(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("✍️ Напиши имя после команды. Пример: /name Vitalii")
        return

    new_name = args[1]
    members = load_members()
    updated = False

    for m in members:
        if m["id"] == message.from_user.id:
            m["custom_name"] = new_name
            updated = True
            break

    if not updated:
        members.append({
            "id": message.from_user.id,
            "username": message.from_user.username,
            "custom_name": new_name
        })

    save_members(members)
    await message.answer(f"✅ Имя изменено на: <b>{new_name}</b>", parse_mode="HTML")

@dp.message(Command("list"))
async def show_list(message: Message):
    members = load_members()
    if not members:
        await message.answer("Список пока пуст 🕳️")
        return

    text = "📋 <b>Список участников:</b>\n\n"
    for i, m in enumerate(members, start=1):
        username = f"@{m['username']}" if m.get("username") else "(без @)"
        name = m.get("custom_name", "")
        text += f"{i}. {username} — {name}\n"

    await message.answer(text, parse_mode="HTML")

async def main():
    print("🚀 Бот запущен и слушает сообщения...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
