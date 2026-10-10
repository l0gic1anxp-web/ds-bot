import asyncio
import random
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from openai import AsyncOpenAI

from config import (
    BOT_TOKEN, ADMIN_ID, AI_API_KEY, AI_MODEL, AI_BASE_URL,
    MIN_DELAY, MAX_DELAY
)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

ai_client = AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)

# ===== ХРАНИЛИЩА =====
pending = {}          # msg_id -> {"user_id", "username", "text", "ai_answer", "ticket_id", "last_msg_id", "notice_id"}
user_dialogs = {}     # user_id -> история
edit_mode = {}        # msg_id заявки -> True
next_id = [1]

# ===== ОЧЕРЕДЬ =====
queue_counter = [1]
active_users = {}
user_queue_pos = {}

# ===== АВТООТПРАВКА =====
AUTO_SEND_DELAY = 60  # секунд (1 минута)


SYSTEM_PROMPT = """Ты — DeepSeek AI, умный и дружелюбный ассистент.
Отвечай структурированно, естественно, на языке пользователя.
Используй markdown и эмодзи умеренно.
Не повторяйся, каждый ответ формулируй по-новому."""


async def ask_ai(user_id: int, text: str) -> str:
    history = user_dialogs.get(user_id, [])
    history.append({"role": "user", "content": text})
    history = history[-10:]

    try:
        resp = await ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
            temperature=1.0,
            max_tokens=800,
        )
        answer = resp.choices[0].message.content
        history.append({"role": "assistant", "content": answer})
        user_dialogs[user_id] = history
        return answer
    except Exception as e:
        logging.error(f"AI error: {e}")
        return "Извините, произошла ошибка при обработке запроса. Попробуйте позже."


def get_queue_text(user_id: int) -> str:
    if user_id not in active_users:
        queue_counter[0] += 1
        active_users[user_id] = queue_counter[0]
        user_queue_pos[user_id] = queue_counter[0]

    pos = active_users[user_id]
    mins = max(1, round(pos * 0.4 + random.uniform(-0.3, 1.2)))

    load = min(99, 40 + pos * 2 + random.randint(-3, 5))
    if load >= 90:
        status = "🔴 Критическая"
    elif load >= 70:
        status = "🟠 Высокая"
    elif load >= 50:
        status = "🟡 Умеренная"
    else:
        status = "🟢 Низкая"

    online = len(active_users) * 3 + random.randint(800, 1500)

    return (
        "⏳ <b>Извините за задержку</b>\n\n"
        "Сейчас наблюдается повышенная нагрузка на серверы.\n\n"
        f"📊 <b>Статус серверов:</b> {status} ({load}%)\n"
        f"👥 <b>Пользователей онлайн:</b> {online}\n"
        f"🎫 <b>Ваша позиция в очереди:</b> #{pos}\n"
        f"⏱ <b>Ожидание:</b> ~{mins} мин.\n\n"
        "Мы обрабатываем ваш запрос. Спасибо за понимание 🙏"
    )


def make_kb(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Отправить", callback_data=f"send:{ticket_id}"),
        InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit:{ticket_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{ticket_id}"),
    ]])


async def auto_send_after_timeout(admin_msg_id: int, delay: int = AUTO_SEND_DELAY):
    """Если ты не ответил за N секунд — бот сам отправляет ответ (без задержки)."""
    await asyncio.sleep(delay)

    if admin_msg_id not in pending:
        return  # уже обработана вручную

    d = pending[admin_msg_id]
    user_id = d["user_id"]
    username = d["username"]
    ai_answer = d["ai_answer"]
    ticket_id = d.get("ticket_id", "?")

    # Убираем, чтобы не отправить дважды
    del pending[admin_msg_id]
    edit_mode.pop(admin_msg_id, None)
    active_users.pop(user_id, None)

    try:
        await bot.send_message(user_id, ai_answer)
        await bot.send_message(
            ADMIN_ID,
            f"🤖 <b>Автоотправка</b> заявки #{ticket_id} → @{username}\n"
            f"(ты не ответил за {delay} сек, бот отправил сам)"
        )
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"❌ Автоотправка #{ticket_id} ошибка: {e}")


def find_ticket_by_ticket_id(ticket_id: int):
    for mid, d in pending.items():
        if d.get("ticket_id") == ticket_id:
            return mid, d
    return None, None


def find_ticket_by_msg(msg_id: int):
    if msg_id in pending:
        return msg_id, pending[msg_id]
    for mid, d in pending.items():
        if d.get("last_msg_id") == msg_id or d.get("notice_id") == msg_id:
            return mid, d
    return None, None


# ===== START =====
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "👋 <b>Привет, админ!</b>\n\n"
            "Все заявки приходят сюда с кнопками.\n"
            f"⏱ Если не ответишь за {AUTO_SEND_DELAY} сек — бот отправит сам.\n\n"
            "<b>Команды:</b>\n"
            "/list — активные заявки\n"
            "/reply &lt;id&gt; &lt;текст&gt; — отправить вручную\n"
            "/edit &lt;id&gt; — редактировать\n"
            "/cancel &lt;id&gt; — отменить\n"
            "/reset — сбросить очередь"
        )
        return

    await message.answer(
        "👋 <b>Привет! Я DeepSeek AI</b> — твой умный помощник.\n\n"
        "Могу помочь с:\n"
        "• Ответами на вопросы\n"
        "• Написанием текстов и кода\n"
        "• Переводом и объяснениями\n\n"
        "Просто напиши мне что-нибудь 👇"
    )


# ===== КОМАНДЫ АДМИНА =====
@dp.message(Command("list"), F.from_user.id == ADMIN_ID)
async def cmd_list(message: types.Message):
    if not pending:
        await message.answer("📭 Активных заявок нет")
        return

    lines = ["📋 <b>Активные заявки:</b>\n"]
    for mid, d in pending.items():
        lines.append(
            f"🆔 Заявка <b>#{d.get('ticket_id', '?')}</b> — @{d['username']}\n"
            f"   💬 {d['text'][:60]}\n"
        )
    await message.answer("\n".join(lines))


@dp.message(Command("reply"), F.from_user.id == ADMIN_ID)
async def cmd_reply(message: types.Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("Использование: <code>/reply &lt;id&gt; &lt;текст&gt;</code>")
        return

    try:
        ticket_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID должен быть числом")
        return

    mid, d = find_ticket_by_ticket_id(ticket_id)
    if d is None:
        await message.answer("❌ Заявка не найдена")
        return

    text = args[2]
    await message.answer(f"⏳ Отправляю @{d['username']}...")

    try:
        await bot.send_message(d["user_id"], text)
        await message.answer(f"✅ Отправлено @{d['username']}")
        del pending[mid]
        edit_mode.pop(mid, None)
        active_users.pop(d["user_id"], None)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("edit"), F.from_user.id == ADMIN_ID)
async def cmd_edit(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: <code>/edit &lt;id&gt;</code>")
        return

    try:
        ticket_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID должен быть числом")
        return

    mid, d = find_ticket_by_ticket_id(ticket_id)
    if d is None:
        await message.answer("❌ Заявка не найдена")
        return

    edit_mode[mid] = True
    notice = await message.answer(
        f"✏️ <b>Режим редактирования заявки #{ticket_id}</b>\n\n"
        f"Отправь новый текст <b>reply (свайп влево)</b> на <b>это сообщение</b>.\n"
        f"Он уйдёт @{d['username']}."
    )
    d["notice_id"] = notice.message_id


@dp.message(Command("cancel"), F.from_user.id == ADMIN_ID)
async def cmd_cancel(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: <code>/cancel &lt;id&gt;</code>")
        return

    try:
        ticket_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID должен быть числом")
        return

    mid, d = find_ticket_by_ticket_id(ticket_id)
    if d is None:
        await message.answer("❌ Заявка не найдена")
        return

    del pending[mid]
    edit_mode.pop(mid, None)
    active_users.pop(d["user_id"], None)
    await message.answer(f"❌ Заявка #{ticket_id} (@{d['username']}) отменена")


@dp.message(Command("reset"), F.from_user.id == ADMIN_ID)
async def cmd_reset(message: types.Message):
    queue_counter[0] = 1
    active_users.clear()
    user_queue_pos.clear()
    await message.answer("🔄 Очередь сброшена")


# ===== /queue ДЛЯ ЮЗЕРА =====
@dp.message(Command("queue"), F.from_user.id != ADMIN_ID)
async def user_queue(message: types.Message):
    uid = message.from_user.id
    if uid not in active_users:
        await message.answer(
            "📭 Вы не в очереди.\n\n"
            "Напишите вопрос — и я поставлю вас в очередь."
        )
        return

    pos = active_users[uid]
    mins = max(1, round(pos * 0.4 + random.uniform(-0.3, 1.2)))
    await message.answer(
        f"🎫 <b>Ваша позиция в очереди:</b> #{pos}\n"
        f"⏱ <b>Ожидание:</b> ~{mins} мин."
    )


# ===== /cancel ДЛЯ ЮЗЕРА =====
@dp.message(Command("cancel"), F.from_user.id != ADMIN_ID)
async def user_cancel(message: types.Message):
    uid = message.from_user.id
    if uid in active_users:
        active_users.pop(uid, None)
        user_queue_pos.pop(uid, None)
        await message.answer(
            "✅ Ваш запрос отменён.\n\n"
            "Если захотите задать новый — просто напишите."
        )
    else:
        await message.answer("📭 У вас нет активных запросов.")


# ===== РЕДАКТИРОВАНИЕ ЧЕРЕЗ REPLY =====
@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message, F.text)
async def admin_reply_handler(message: types.Message):
    reply_to_id = message.reply_to_message.message_id
    new_text = message.text

    mid, d = find_ticket_by_msg(reply_to_id)

    if d is None:
        active_edits = [m for m, v in edit_mode.items() if v and m in pending]
        if len(active_edits) == 1:
            mid = active_edits[0]
            d = pending[mid]

    if d is None:
        return

    ticket_id = d.get("ticket_id", "?")
    await message.answer(f"⏳ Отправляю @{d['username']}...")

    try:
        await bot.send_message(d["user_id"], new_text)
        await message.answer(f"✅ Заявка #{ticket_id} отправлена @{d['username']}")
        del pending[mid]
        edit_mode.pop(mid, None)
        active_users.pop(d["user_id"], None)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# ===== СООБЩЕНИЕ ОТ ДРУГА =====
@dp.message(F.from_user.id != ADMIN_ID)
async def handle_user(message: types.Message):
    user = message.from_user

    if message.text and message.text.startswith("/"):
        return

    text = message.text or "(не текст)"

    await message.answer(get_queue_text(user.id))

    ai_answer = await ask_ai(user.id, text)

    ticket_id = next_id[0]
    next_id[0] += 1

    header = (
        f"🆔 <b>Заявка #{ticket_id}</b>\n\n"
        f"👤 <b>От:</b> {user.full_name} (@{user.username or 'без_ника'})\n"
        f"🆔 <code>{user.id}</code>\n"
        f"🎫 <b>Позиция:</b> #{active_users.get(user.id, '?')}\n\n"
        f"💬 <b>Вопрос:</b>\n{text}\n\n"
        f"🤖 <b>Ответ ИИ:</b>\n{ai_answer}"
    )

    sent = await bot.send_message(
        ADMIN_ID, header, reply_markup=make_kb(ticket_id),
        disable_web_page_preview=True
    )

    pending[sent.message_id] = {
        "user_id": user.id,
        "username": user.username or "без_ника",
        "text": text,
        "ai_answer": ai_answer,
        "ticket_id": ticket_id,
        "last_msg_id": sent.message_id,
        "notice_id": None,
    }

    # Автоотправка через 1 минуту, если не ответишь
    asyncio.create_task(auto_send_after_timeout(sent.message_id, delay=AUTO_SEND_DELAY))


# ===== КНОПКИ =====
@dp.callback_query(F.from_user.id == ADMIN_ID)
async def admin_action(call: CallbackQuery):
    action, ticket_str = call.data.split(":")
    ticket_id = int(ticket_str)
    msg_id = call.message.message_id

    mid, d = find_ticket_by_msg(msg_id)
    if d is None:
        mid, d = find_ticket_by_ticket_id(ticket_id)

    if d is None:
        await call.answer("Заявка уже обработана", show_alert=True)
        return

    if action == "send":
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Отправляю...")
        try:
            await bot.send_message(d["user_id"], d["ai_answer"])
            await call.message.answer(f"✅ Заявка #{ticket_id} отправлена @{d['username']}")
            del pending[mid]
            edit_mode.pop(mid, None)
            active_users.pop(d["user_id"], None)
        except Exception as e:
            await call.message.answer(f"❌ Ошибка: {e}")

    elif action == "edit":
        await call.answer("Режим редактирования")
        edit_mode[mid] = True
        notice = await call.message.answer(
            f"✏️ <b>Заявка #{ticket_id}</b>\n\n"
            f"Отправь новый текст <b>reply (свайп влево)</b> на <b>это сообщение</b>.\n"
            f"Уйдёт @{d['username']}.\n\n"
            f"⚠️ Автоотправка через {AUTO_SEND_DELAY} сек всё равно сработает, "
            f"если не ответишь."
        )
        d["notice_id"] = notice.message_id

    elif action == "reject":
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Отклонено")
        await call.message.answer(f"❌ Заявка #{ticket_id} (@{d['username']}) отклонена")
        del pending[mid]
        edit_mode.pop(mid, None)
        active_users.pop(d["user_id"], None)


async def main():
    print("🤖 Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
