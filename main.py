import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
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


def clear_chat(chat_id: int):
    return supabase.table("members").delete().eq("chat_id", chat_id).execute()


# ============ UTILS ============

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ============ COMMANDS ============

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)

    role = "Админ" if is_admin(msg.from_user.id) else "Участник"

    await msg.answer(
        f"👋 Привет! Ваша роль: <b>{role}</b>\n\n"
        "/join — записаться в список\n"
        "/list — показать список\n"
        "/name ИМЯ — установить имя из другого сервиса\n"
        "/remove — удалить себя\n"
        "/clear — очистить список (админ)"
        , parse_mode="HTML"
    )


@dp.message(Command("join"))
async def cmd_join(msg: types.Message):
    await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)
    await msg.answer(f"✅ {msg.from_user.full_name} добавлен в список!")


@dp.message(Command("list"))
async def cmd_list(msg: types.Message):
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

    await msg.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("name"))
async def cmd_name(msg: types.Message):
    args = msg.text.split(maxsplit=1)

    if len(args) < 2:
        await msg.answer("✏️ Напиши имя после команды. Пример: /name DragonHunter")
        return

    external_name = args[1].strip()

    await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user, external_name)

    await msg.answer(f"✅ Имя из другого сервиса установлено: {external_name}")

# ========== ADMIN: SET NAME FOR ANOTHER USER ==========

@dp.message(Command("setname"))
async def admin_set_name(msg: types.Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только администратор может менять имена другим участникам.")
        return

    args = msg.text.split(maxsplit=2)

    if len(args) < 3:
        await msg.answer("❗ Формат: /setname @username НовоеИмя\nпример: /setname @vitalii Hunter")
        return

    target, new_name = args[1], args[2].strip()

    # Убираем @ если есть
    if target.startswith("@"):
        target_username = target[1:]
        target_user_id = None
    else:
        target_username = None
        try:
            target_user_id = int(target)
        except:
            await msg.answer("❌ Ошибка: укажите @username или user_id.")
            return

    # --- Найдём участника в базе ---
    if target_username:
        result = (
            supabase.table("members")
            .select("*")
            .eq("chat_id", msg.chat.id)
            .eq("username", target_username)
            .execute()
        )
    else:
        result = (
            supabase.table("members")
            .select("*")
            .eq("chat_id", msg.chat.id)
            .eq("user_id", target_user_id)
            .execute()
        )

    rows = result.data or []

    if not rows:
        await msg.answer("❌ Пользователь не найден в базе.")
        return

    user_row = rows[0]
    uid = user_row["user_id"]

    # --- Обновляем external_name ---
    supabase.table("members") \
        .update({"external_name": new_name}) \
        .eq("chat_id", msg.chat.id) \
        .eq("user_id", uid) \
        .execute()

    uname = user_row["username"]
    fname = user_row["full_name"]

    display = f"@{uname}" if uname else fname

    await msg.answer(f"✅ Имя участника <b>{display}</b> изменено на: <b>{new_name}</b>", parse_mode="HTML")

# ========== CONFIRM REMOVE ==========

@dp.message(Command("remove"))
async def confirm_remove(msg: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да, удалить", callback_data="remove_yes")],
        [InlineKeyboardButton(text="Нет", callback_data="remove_no")]
    ])
    await msg.answer("❓ Вы уверены, что хотите удалить себя из списка?", reply_markup=kb)


@dp.callback_query(lambda c: c.data == "remove_yes")
async def remove_yes(callback: types.CallbackQuery):
    await asyncio.to_thread(delete_user, callback.message.chat.id, callback.from_user.id)
    await callback.message.edit_text("🗑 Вы удалены из списка!")
    await callback.answer()


@dp.callback_query(lambda c: c.data == "remove_no")
async def remove_no(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Удаление отменено.")
    await callback.answer()


# ========== CONFIRM CLEAR ==========

@dp.message(Command("clear"))
async def confirm_clear(msg: types.Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Только админ может очистить список!")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да, очистить", callback_data="clear_yes")],
        [InlineKeyboardButton(text="Нет", callback_data="clear_no")]
    ])
    await msg.answer("❓ Точно очистить весь список?", reply_markup=kb)


@dp.callback_query(lambda c: c.data == "clear_yes")
async def clear_yes(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа!", show_alert=True)
        return

    await asyncio.to_thread(clear_chat, callback.message.chat.id)
    await callback.message.edit_text("🧹 Список полностью очищен!")
    await callback.answer()


@dp.callback_query(lambda c: c.data == "clear_no")
async def clear_no(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Очистка отменена.")
    await callback.answer()


# ============ AUTO REGISTER ============

@dp.message()
async def auto_register(msg: types.Message):
    if msg.from_user:
        try:
            await asyncio.to_thread(upsert_user, msg.chat.id, msg.from_user)
        except Exception as e:
            print("Supabase error:", e)


# ============ RUN ============

async def main():
    print("BOT STARTED OK")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
