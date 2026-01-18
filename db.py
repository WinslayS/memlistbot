from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY
from logger import logger
from aiogram import types

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============ DB HELPERS ============
def upsert_user(chat_id: int, user: types.User, external_name=None, extra_role=None):
    if user.username == "GroupAnonymousBot" or (user.is_bot and user.id != chat_id):
        return

    # === 1. SELECT ===
    try:
        res = (
            supabase.table("members")
            .select("*")
            .eq("chat_id", chat_id)
            .eq("user_id", user.id)
            .execute()
        )

        if res and isinstance(res.data, list) and len(res.data) > 0:
            row = res.data[0]
        else:
            row = None

    except Exception as e:
        logger.error("Supabase SELECT error: %s", e)
        row = None

    # === 2. Если НЕТ записи — создаём ===
    if not row:
        payload = {
            "chat_id": chat_id,
            "user_id": user.id,
            "username": user.username or "",
            "full_name": user.full_name or "",
            "external_name": external_name or "",
            "extra_role": extra_role or "",
        }

        try:
            supabase.table("members").insert(payload).execute()
        except Exception as e:
            logger.error("Supabase INSERT error: %s", e)

        return

    # === 3. Если запись есть — обновляем только изменившиеся поля ===
    update_data = {}
    new_username = user.username or ""
    new_full_name = user.full_name or ""

    if row.get("username") != new_username:
        update_data["username"] = new_username

    if row.get("full_name") != new_full_name:
        update_data["full_name"] = new_full_name

    # НЕ трогаем external_name, если None
    if external_name is not None:
        if external_name != (row.get("external_name") or ""):
            update_data["external_name"] = external_name

    if extra_role is not None:
        update_data["extra_role"] = extra_role

    # --- UPDATE с обработкой ошибок ---
    if not update_data:
        return

    try:
        (
            supabase.table("members")
            .update(update_data)
            .eq("chat_id", chat_id)
            .eq("user_id", user.id)
            .execute()
        )
    except Exception as e:
        logger.error("Supabase upsert_user FIXED error: %s", e)

def get_members(chat_id: int):
    try:
        res = (
            supabase.table("members")
            .select("*")
            .eq("chat_id", chat_id)
            .order("id")
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error("Supabase get_members error: %s", e)
        return []

def delete_user(chat_id: int, user_id: int):
    try:
        (
            supabase.table("members")
            .delete()
            .eq("chat_id", chat_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.error("delete_user error: %s", e)

def clear_left_users(chat_id: int, left_user_ids: list[int]):
    for uid in left_user_ids:
        try:
            supabase.table("members") \
                .delete() \
                .eq("chat_id", chat_id) \
                .eq("user_id", uid) \
                .execute()

            logger.info("Удалён из базы ушедший пользователь %s из чата %s", uid, chat_id)

        except Exception as e:
            logger.error("Supabase clear_left_users error (chat %s user %s): %s", chat_id, uid, e)
