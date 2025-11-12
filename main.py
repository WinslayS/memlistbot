import json
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.filters import Command

TOKEN = "8559168291:AAHTWpAoSD1rtKHkCXWcIvcvSLPCBJpD0CM"
DATA_FILE = "members.json"

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

def load_members():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_members(members):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(members, f, ensure_ascii=False, indent=2)

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.answer("Привет! Напиши /join чтобы записаться, или /list чтобы увидеть список.")

@dp.message_handler(commands=["join"])
async def join(message: types.Message):
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
        await message.answer("Ты уже в списке 🙂")

@dp.message_handler(commands=["list"])
async def show_list(message: types.Message):
    members = load_members()
    if not members:
        await message.answer("Список пока пуст.")
        return

    text = "📋 <b>Список участников:</b>\n\n"
    for i, m in enumerate(members, start=1):
        if m["username"]:
            text += f"{i}. @{m['username']}\n"
        else:
            text += f"{i}. {m['name']} (без @)\n"

    await message.answer(text, parse_mode="HTML")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
