import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from supabase import create_client, Client

# ============ ENV ============

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing BOT_TOKEN or SUPABASE_URL or SUPABASE_KEY in env variables")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# владелец бота (для /clear)
OWNER_ID = 8523019691


# ============ DB HELPERS ============

def upsert_user(chat_id: int, user: types.User, external_name: str | None = None):
    """
    Главное место, где раньше была ошибка:
    ТЕПЕРЬ тут upsert c on_conflict по (chat_id, user_id),
    поэтому уникальный индекс не ломается.
    """

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
        on_conflict="chat_id, user_id"    # <= ВАЖНО
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


def clear_chat(chat_id: int):
    return supabase.table("members").delete().eq("chat_id", chat_id).execute()


# ============ COMMANDS ============

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    # сразу регистрируем/обновляем пользователя
    await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)

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
    await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)
    await msg.answer(f"✅ {msg.from_user.full_name} добавлен в список!")


@dp.message(Command("list"))
async def cmd_list(msg: types.Message):
    # Обновим данные отправителя (username / full_name)
    await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)

    rows = await asyncio.to_thread(get_members, msg.chat.id)

    if not rows:
        await msg.answer("Список пуст 🕳️")
        return

    lines = ["📋 <b>Список участников:</b>\n"]
    for i, row in enumerate(rows, start=1):
        full_name = row.get("full_name") or "Без имени"
        username = row.get("username") or ""
        external = row.get("external_name") or ""

        username_part = f" (@{username})" if username else ""
        external_part = f" — {external}" if external else ""

        lines.append(f"{i}. {full_name}{username_part}{external_part}")

    text = "\n".join(lines)
    await msg.answer(text, parse_mode="HTML")


@dp.message(Command("name"))
async def cmd_name(msg: types.Message):
    args = msg.text.split(maxsplit=1)

    if len(args) < 2 or not args[1].strip():
        await msg.answer("✏️ Напиши имя после команды. Пример: /name DragonHunter")
        return

    external_name = args[1].strip()

    # сохраняем external_name + обновляем username/full_name
    await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user, external_name)

    await msg.answer(f"✅ Имя из другого сервиса установлено: {external_name}")


@dp.message(Command("remove"))
async def cmd_remove(msg: types.Message):
    await asyncio.to_thread(delete_user, msg.chat.id, msg.from_user.id)
    await msg.answer("🗑 Ты удалён из списка!")


@dp.message(Command("clear"))
async def cmd_clear(msg: types.Message):
    if msg.from_user.id != OWNER_ID:
        await msg.answer("⛔ Только владелец бота может очистить список!")
        return

    await asyncio.to_thread(clear_chat, msg.chat.id)
    await msg.answer("🧹 Список полностью очищен!")


# ============ AUTO-REGISTRATION ============

@dp.message()  # любой апдейт, если это не команда выше
async def auto_register(msg: types.Message):
    """
    1) создаём запись для любого пользователя, который что-то пишет;
    2) каждый раз обновляем username / full_name;
    3) за счёт upsert и уникального индекса дублей не будет.
    """
    if not msg.from_user:
        return

    try:
        await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)
    except Exception as e:
        # чтобы не падал бот, если вдруг что-то не так в Supabase
        print("Supabase error in auto_register:", e)


# ============ RUN ============

async def main():
    print("BOT STARTED OK")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
