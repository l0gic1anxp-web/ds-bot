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
pending = {}          # admin_msg_id -> {"user_id", "username", "text", "ai_answer"}
user_dialogs = {}     # user_id -> [история]
edit_mode = {}        # admin_msg_id -> True (ждём ручной ввод)
next_id = [1]         # счётчик заявок


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


def queue_text() -> str:
    pos = random.randint(3, 18)
    mins = random.randint(1, 4)
    return (
        "⏳ <b>Извините за задержку</b>\n\n"
        "Сейчас наблюдается высокая нагрузка на серверы.\n"
        f"Ваш запрос добавлен в очередь: позиция <b>#{pos}</b>\n"
        f"Примерное время ожидания: ~{mins} мин.\n\n"
        "Спасибо за понимание 🙏"
    )


def make_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Отправить", callback_data=f"send:{user_id}"),
        InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit:{user_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{user_id}"),
    ]])


# ===== START =====
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "👋 <b>Привет, админ!</b>\n\n"
            "Все заявки от друзей приходят сюда с кнопками.\n\n"
            "<b>Команды:</b>\n"
            "/list — активные заявки\n"
            "/reply &lt;id&gt; &lt;текст&gt; — отправить вручную\n"
            "/edit &lt;id&gt; — редактировать\n"
            "/cancel &lt;id&gt; — отменить"
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
            f"🆔 <code>{mid}</code> — @{d['username']}\n"
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
        mid = int(args[1])
    except ValueError:
        await message.answer("❌ ID должен быть числом")
        return

    if mid not in pending:
        await message.answer("❌ Заявка не найдена")
        return

    d = pending[mid]
    text = args[2]

    await message.answer(f"⏳ Отправляю @{d['username']}...")
    await asyncio.sleep(random.randint(MIN_DELAY, MAX_DELAY))

    try:
        await bot.send_message(d["user_id"], text)
        await message.answer(f"✅ Отправлено @{d['username']}")
        del pending[mid]
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("edit"), F.from_user.id == ADMIN_ID)
async def cmd_edit(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: <code>/edit &lt;id&gt;</code>")
        return

    try:
        mid = int(args[1])
    except ValueError:
        await message.answer("❌ ID должен быть числом")
        return

    if mid not in pending:
        await message.answer("❌ Заявка не найдена")
        return

    edit_mode[mid] = True
    d = pending[mid]
    await message.answer(
        f"✏️ <b>Режим редактирования заявки {mid}</b>\n\n"
        f"Пришли новый текст <b>ответом (reply)</b> на <b>это сообщение</b>.\n"
        f"Он уйдёт @{d['username']}."
    )


@dp.message(Command("cancel"), F.from_user.id == ADMIN_ID)
async def cmd_cancel(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: <code>/cancel &lt;id&gt;</code>")
        return

    try:
        mid = int(args[1])
    except ValueError:
        await message.answer("❌ ID должен быть числом")
        return

    if mid in pending:
        d = pending[mid]
        del pending[mid]
        edit_mode.pop(mid, None)
        await message.answer(f"❌ Заявка {mid} (@{d['username']}) отменена")
    else:
        await message.answer("❌ Заявка не найдена")


# ===== РЕДАКТИРОВАНИЕ ЧЕРЕЗ REPLY =====
@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message, F.text)
async def admin_reply_handler(message: types.Message):
    reply_to_id = message.reply_to_message.message_id
    new_text = message.text

    # Ищем заявку по reply на сообщение бота
    # Случай 1: reply на сообщение с заявкой
    # Случай 2: reply на сообщение "Режим редактирования"
    # Находим ID заявки по совпадению или по edit_mode

    target_id = None

    # Ищем в pending по сохранённому notice_id
    for mid, d in pending.items():
        if d.get("notice_id") == reply_to_id:
            target_id = mid
            break

    # Или reply прямо на сообщение с кнопками (последний msg)
    if target_id is None:
        for mid, d in pending.items():
            if d.get("last_msg_id") == reply_to_id:
                target_id = mid
                break

    # Или простой случай: reply на уведомление, которое является последним
    if target_id is None:
        # Проверяем edit_mode — если есть активный, берём его
        active_edits = [mid for mid, v in edit_mode.items() if v]
        if len(active_edits) == 1:
            target_id = active_edits[0]

    if target_id is None:
        return

    d = pending[target_id]
    await message.answer(f"⏳ Отправляю @{d['username']}...")
    await asyncio.sleep(random.randint(MIN_DELAY, MAX_DELAY))

    try:
        await bot.send_message(d["user_id"], new_text)
        await message.answer(f"✅ Отправлено @{d['username']}")
        del pending[target_id]
        edit_mode.pop(target_id, None)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# ===== СООБЩЕНИЕ ОТ ДРУГА =====
@dp.message(F.from_user.id != ADMIN_ID)
async def handle_user(message: types.Message):
    user = message.from_user
    text = message.text or "(не текст)"

    await message.answer(queue_text())

    ai_answer = await ask_ai(user.id, text)

    ticket_id = next_id[0]
    next_id[0] += 1

    header = (
        f"🆔 <b>Заявка #{ticket_id}</b>\n\n"
        f"👤 <b>От:</b> {user.full_name} (@{user.username or 'без_ника'})\n"
        f"🆔 <code>{user.id}</code>\n\n"
        f"💬 <b>Вопрос:</b>\n{text}\n\n"
        f"🤖 <b>Ответ ИИ:</b>\n{ai_answer}"
    )

    sent = await bot.send_message(
        ADMIN_ID, header, reply_markup=make_kb(user.id),
        disable_web_page_preview=True
    )

    pending[sent.message_id] = {
        "user_id": user.id,
        "username": user.username or "без_ника",
        "text": text,
        "ai_answer": ai_answer,
        "last_msg_id": sent.message_id,
        "notice_id": None,
    }


# ===== КНОПКИ =====
@dp.callback_query(F.from_user.id == ADMIN_ID)
async def admin_action(call: CallbackQuery):
    action, user_id_str = call.data.split(":")
    user_id = int(user_id_str)
    msg_id = call.message.message_id

    # Находим заявку по msg_id
    target = None
    for mid, d in pending.items():
        if d.get("last_msg_id") == msg_id or mid == msg_id:
            target = mid
            break

    if target is None:
        await call.answer("Заявка уже обработана", show_alert=True)
        return

    d = pending[target]

    if action == "send":
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Отправляю...")
        await asyncio.sleep(random.randint(MIN_DELAY, MAX_DELAY))
        try:
            await bot.send_message(d["user_id"], d["ai_answer"])
            await call.message.answer(f"✅ Ответ отправлен @{d['username']}")
            del pending[target]
        except Exception as e:
            await call.message.answer(f"❌ Ошибка: {e}")

    elif action == "edit":
        await call.answer("Режим редактирования")
        edit_mode[target] = True
        notice = await call.message.answer(
            f"✏️ <b>Заявка #{target}</b>\n\n"
            f"Отправь новый текст <b>reply (свайп влево)</b> на <b>это сообщение</b>.\n"
            f"Уйдёт @{d['username']}."
        )
        d["notice_id"] = notice.message_id

    elif action == "reject":
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Отклонено")
        await call.message.answer(f"❌ Ответ для @{d['username']} отклонён")
        del pending[target]
        edit_mode.pop(target, None)


async def main():
    print("🤖 Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
