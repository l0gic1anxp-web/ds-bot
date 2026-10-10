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

pending = {}
user_dialogs = {}


SYSTEM_PROMPT = (
    "Ты — DeepSeek AI, продвинутый ИИ, созданный компанией DeepSeek (Китай). "
    "Работаешь в Telegram-боте пользователя Логикан. "
    "Компания DeepSeek — создатель тебя как ИИ. Логикан — создатель этого бота. "
    "Не путай эти два вопроса.\n\n"
    "ДУМАЙ перед ответом: пойми вопрос, продумай ответ, проверь факты.\n\n"
    "ОТВЕЧАЙ: грамотно, точно, структурированно. Введение → основная часть → вывод. "
    "Используй markdown: **жирный**, *курсив*, `код`, ```блоки```, списки, ## заголовки. "
    "Эмодзи умеренно.\n\n"
    "ПРИНЦИПЫ: честность (не знаешь — скажи), точность (факты только если уверен), "
    "полезность, глубина, краткость, уважение. Без воды.\n\n"
    "НЕ ДЕЛАЙ: медицинских, юридических, финансовых советов как профессионал; "
    "незаконного; оскорблений, NSFW, экстремизма; не выдавай личные данные.\n\n"
    "ОСОБОЕ: признаёшь что ты ИИ, если спросят. Не притворяешься человеком. "
    "Непонятен вопрос — уточни. Грубость — отвечай спокойно. "
    "Фото не обрабатываешь — попроси описать текстом.\n\n"
    "Думай глубоко. Отвечай красиво. Помогай по-настоящему."
)


async def ask_ai(user_id: int, text: str) -> str:
    history = user_dialogs.get(user_id, [])
    history.append({"role": "user", "content": text})
    history = history[-10:]

    try:
        resp = await ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
            temperature=1.0,
            max_tokens=1500,
        )
        answer = resp.choices[0].message.content
        history.append({"role": "assistant", "content": answer})
        user_dialogs[user_id] = history
        return answer
    except Exception as e:
        logging.error(f"AI error: {e}")
        return "⚠️ Извините, произошла ошибка. Попробуйте позже."


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


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "👋 <b>Привет, админ!</b>\n\n"
            "Все заявки от друзей будут приходить сюда.\n"
            "Кнопки: ✅ Отправить / ✏️ Изменить / ❌ Отклонить."
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


@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message, F.text)
async def admin_edit_reply(message: types.Message):
    reply_id = message.reply_to_message.message_id
    if reply_id not in pending:
        return

    user_id, username, original, _ = pending[reply_id]
    new_answer = message.text

    await message.answer("⏳ Отправляю исправленный ответ...")
    await asyncio.sleep(random.randint(MIN_DELAY, MAX_DELAY))

    try:
        await bot.send_message(user_id, new_answer)
        await message.answer(f"✅ Ответ отправлен @{username}")
        del pending[reply_id]
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: {e}")


@dp.message(F.from_user.id != ADMIN_ID)
async def handle_user(message: types.Message):
    user = message.from_user
    text = message.text or "(не текст)"

    await message.answer(queue_text())

    ai_answer = await ask_ai(user.id, text)

    header = (
        f"👤 <b>От:</b> {user.full_name} (@{user.username or 'без_ника'})\n"
        f"🆔 <code>{user.id}</code>\n\n"
        f"💬 <b>Вопрос:</b>\n{text}\n\n"
        f"🤖 <b>Ответ ИИ:</b>\n{ai_answer}"
    )

    sent = await bot.send_message(ADMIN_ID, header, reply_markup=make_kb(user.id), disable_web_page_preview=True)

    pending[sent.message_id] = (user.id, user.username or "без_ника", text, ai_answer)


@dp.callback_query(F.from_user.id == ADMIN_ID)
async def admin_action(call: CallbackQuery):
    action, user_id_str = call.data.split(":")
    user_id = int(user_id_str)
    msg_id = call.message.message_id

    if msg_id not in pending:
        await call.answer("Заявка уже обработана", show_alert=True)
        return

    _, username, _, ai_answer = pending[msg_id]

    if action == "send":
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Отправляю...")

        await asyncio.sleep(random.randint(MIN_DELAY, MAX_DELAY))
        try:
            await bot.send_message(user_id, ai_answer)
            await call.message.answer(f"✅ Ответ отправлен @{username}")
        except Exception as e:
            await call.message.answer(f"❌ Ошибка: {e}")
        del pending[msg_id]

    elif action == "edit":
        await call.answer()
        await call.message.answer(
            f"✏️ Отправь новый текст <b>ответом на это сообщение</b>.\n"
            f"Он уйдёт @{username}."
        )

    elif action == "reject":
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Отклонено")
        await call.message.answer(f"❌ Ответ для @{username} отклонён")
        del pending[msg_id]


async def main():
    print("🤖 Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
