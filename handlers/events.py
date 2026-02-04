import asyncio
import time

from aiogram import types
from aiogram.enums import ChatMemberStatus

from core import bot, dp
from logger import logger
from db import upsert_user, delete_user
from helpers import WELCOME_SENT, WELCOME_TTL

@dp.my_chat_member()
async def on_bot_chat_member(event: types.ChatMemberUpdated):
    chat_id = event.chat.id
    user = event.new_chat_member.user

    if user.id != bot.id:
        return

    old = event.old_chat_member.status
    new = event.new_chat_member.status

    if new in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR
    ) and old in (
        ChatMemberStatus.LEFT,
        ChatMemberStatus.KICKED
    ):

        await bot.send_message(
            chat_id,
            "🤖 <b>Бот подключён!</b>\n\n"
            "Чтобы всё работало корректно:\n"
            "• дайте мне право <b>«Добавление администраторов»</b>\n"
            "• отключите <b>анонимность администраторов</b>\n"
            "• команды пишите <b>без пробела после слэша</b>\n"
            "• данные собираются с момента добавления бота\n\n"
            "После этого все функции будут работать корректно.",
            parse_mode="HTML"
        )

        now = time.time()
        last = WELCOME_SENT.get(chat_id, 0)

        if now - last > WELCOME_TTL:
            WELCOME_SENT[chat_id] = now

            await bot.send_message(
                chat_id,
                (
                    "👋 <b>Привет! Вот краткая справка по боту:</b>\n\n"
                    "📌 <b>Команды:</b>\n"
                    "/list — показать список участников\n"
                    "/name [имя] — установить своё имя\n"
                    "/find [имя/@] — поиск участника\n"
                    "/setname [@] [имя] — назначить имя другому (админ)\n"
                    "/export — экспорт списка (админ)\n"
                    "/cleanup — очистить список ушедших (админ)\n"
                    "/add [роль] — установить себе роль (участник)\n"
                    "/addrole [@] [роль] — назначить роль другому участнику (админ)\n\n"
                    "📖 <b>Как добавить участника:</b>\n"
                    "• Если есть username (@) в базе данных (автоматически при заходе):\n"
                    "  <code>/setname @username Имя</code>\n\n"
                    "• Если username нет:\n"
                    "  1) участник пишет любое сообщение в чат\n"
                    "  2) админ отвечает на это сообщение:\n"
                    "     <code>/setname Имя</code>\n\n"
                    "• Если участник хочет сам установить имя:\n"
                    "  <code>/name Имя</code>\n\n"
                    "📖 <b>Обозначения:</b>\n"
                    "• <code>[@]</code> — username участника\n"
                    "• <code>[имя]</code> — любое текстовое имя\n\n"
                    "📖 <b>Сортировка (добавляется к /list [], /export []:</b>\n"
                    "• <b>[]</b> — по дате\n"
                    "• <b>[n]</b> — по имени (full_name)\n"
                    "• <b>[u]</b> — по @ (username)\n"
                    "• <b>[e]</b> — по заданному имени (external_name)\n"
                ),
                parse_mode="HTML"
            )

        return

@dp.chat_member()
async def chat_member_events(event: types.ChatMemberUpdated):
    old = event.old_chat_member.status
    new = event.new_chat_member.status
    user = event.new_chat_member.user
    chat_id = event.chat.id

    INSIDE_STATUSES = {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.RESTRICTED,
    }
    OUTSIDE_STATUSES = {
        ChatMemberStatus.LEFT,
        ChatMemberStatus.KICKED,
    }

    if (
        old in OUTSIDE_STATUSES and new in INSIDE_STATUSES
    ) or (
        old == ChatMemberStatus.MEMBER
        and new == ChatMemberStatus.MEMBER
        and event.invite_link is not None
    ):
        if user.username == "GroupAnonymousBot" or user.is_bot:
            return

        await asyncio.to_thread(upsert_user, chat_id, user)

        logger.info(
            "Пользователь %s (%s) добавлен в список чата %s",
            user.id, user.username, chat_id
        )

        await send_welcome(event, user)
        return

    if new in OUTSIDE_STATUSES:
        await asyncio.to_thread(delete_user, chat_id, user.id)

        logger.info(
            "Пользователь %s удалён из списка чата %s",
            user.id, chat_id
        )
        return

async def send_welcome(event: types.ChatMemberUpdated, user: types.User):
    chat_id = event.chat.id

    text = (
        f"👋 Привет, <b>{user.full_name}</b>!\n\n"
        "Чтобы появиться в списке, используй:\n"
        "• <code>/name ТвоёИмя</code>\n"
        "• <code>/add Роль</code> (необязательно)\n\n"
        "Если что-то непонятно — /help 🙂"
    )

    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        logger.error("WELCOME ERROR: %s", e)
