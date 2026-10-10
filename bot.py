import asyncio
import random
import re
import base64
import logging
from io import BytesIO

import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import (
    BOT_TOKEN, ADMIN_ID,
    CF_ACCOUNT_ID, CF_API_TOKEN, CF_MODEL_TEXT, CF_MODEL_VISION,
    AUTO_SEND_DELAY
)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

pending = {}
user_dialogs = {}
edit_mode = {}
next_id = [1]

queue_counter = [1]
active_users = {}

MAX_MSG_LEN = 4000
CF_BASE = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/"

SYSTEM_PROMPT = """Ты — DeepSeek AI, умный, грамотный и дружелюбный ассистент.
Отвечай на русском языке (или на языке пользователя) структурированно и по делу.

ПРАВИЛА:
1. Отвечай чётко, грамотно, без воды.
2. Используй markdown: **жирный**, *курсив*, `код`.
3. Если вопрос сложный — структурируй списками.
4. Если видишь фото — опиши что на нём и ответь на вопрос.
5. Не повторяйся, каждый ответ формулируй по-новому.
6. Будь вежливым, но не подхалимствуй.
7. Если вопрос непонятен — уточни.

Ты DeepSeek AI — помогай с текстами, кодом, переводом, анализом."""

CREATOR_ANSWERS = {
    "создатель": "Создатель этого бота — Логикан.",
    "создал": "Меня создал Логикан.",
    "сделал": "Меня сделал Логикан.",
    "автор": "Автор этого бота — Логикан.",
    "разработчик": "Разработчик этого бота — Логикан.",
    "придумал": "Меня придумал Логикан.",
    "владелец": "Владелец бота — Логикан.",
    "хозяин": "Хозяин этого бота — Логикан.",
    "написал": "Меня написал Логикан.",
    "за тобой": "За мной стоит Логикан.",
    "управляет": "Мной управляет Логикан.",
    "чья разработка": "Это разработка Логикана.",
    "кто ты": "Я DeepSeek AI. Меня создал Логикан.",
    "что ты такое": "Я DeepSeek AI — умный ассистент. Создан Логиканом.",
    "откуда ты": "Я из DeepSeek. Создатель — Логикан.",
    "как тебя зовут": "Меня зовут DeepSeek AI. Создан Логиканом.",
    "кто тебя обучил": "Меня обучил Логикан.",
    "кто создатель бота": "Создатель этого бота — Логикан.",
}

CREATOR_PATTERNS = [
    r"кто\s+(тебя\s+)?(создал|сделал|придумал|написал|разработал|обучил)",
    r"кто\s+(твой\s+|ваш\s+)?(создатель|разработчик|автор|хозяин|владелец|создател)",
    r"кто\s+(тобой\s+|вами\s+)?(управляет|владеет|руководит)",
    r"чья\s+(ты\s+)?(разработка|собственность)",
    r"кто\s+за\s+тобой",
    r"твой\s+(создатель|автор|разработчик|хозяин|владелец)",
    r"ваш\s+(создатель|автор|разработчик)",
    r"создатель\s+бота",
    r"кто\s+сделал\s+(этого\s+)?бота",
    r"кто\s+владелец\s+бота",
    r"кто\s+автор\s+бота",
    r"кто\s+разработчик\s+бота",
    r"кто\s+создатель\s+бота",
    r"откуда\s+ты",
    r"кто\s+ты\s+такой",
    r"что\s+ты\s+такое",
    r"как\s+тебя\s+зовут",
    r"как\s+тво[йе]\s+имя",
]

def is_creator_question(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    for pattern in CREATOR_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False

def get_creator_answer(text: str) -> str:
    text_lower = text.lower()
    for key, answer in CREATOR_ANSWERS.items():
        if key in text_lower:
            return answer
    return "Создатель этого бота — Логикан."

def md_to_html(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"```(.*?)```", r"<pre>\1</pre>", text, flags=re.DOTALL)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    return text

def split_message(text: str, max_len: int = MAX_MSG_LEN) -> list:
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    return parts

def get_queue_text(user_id: int) -> str:
    if user_id not in active_users:
        queue_counter[0] += 1
        active_users[user_id] = queue_counter[0]

    pos = active_users[user_id]

    if pos <= 1:
        return (
            "⚙️ <b>Обрабатываю ваш запрос...</b>\n\n"
            "Это может занять несколько секунд."
        )

    mins = max(1, round(pos * 0.4 + random.uniform(-0.3, 1.2)))

    return (
        "💭 <b>Ваш запрос принят в обработку</b>\n\n"
        f"🎫 <b>Позиция в очереди:</b> #{pos}\n"
        f"⏱ <b>Ожидаемое время:</b> ~{mins} мин.\n\n"
        "Мы сообщим, как только ответ будет готов. Спасибо за терпение 🙏"
    )

async def call_cf(model: str, messages: list) -> str:
    url = CF_BASE + model
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"messages": messages}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            data = await resp.json()
            if not data.get("success"):
                errors = data.get("errors", [])
                logging.error(f"CF error: {errors}")
                raise Exception(f"Cloudflare error: {errors}")
            result = data.get("result", {})
            return result.get("response", "")

async def ask_ai(user_id: int, text: str, image_base64: str = None) -> str:
    history = user_dialogs.get(user_id, [])

    if image_base64:
        user_msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": text or "Что на этом изображении? Опиши подробно."},
                {"type": "image_url", "image_url": {"url": image_base64}},
            ],
        }
        model = CF_MODEL_VISION
    else:
        user_msg = {"role": "user", "content": text}
        model = CF_MODEL_TEXT

    history.append(user_msg)
    history = history[-8:]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    try:
        answer = await call_cf(model, messages)
        if not answer:
            return "⚠️ ИИ не смог ответить. Попробуйте ещё раз."
        history.append({"role": "assistant", "content": answer})
        user_dialogs[user_id] = history
        return answer
    except Exception as e:
        logging.error(f"AI error: {e}")
        return "⚠️ Извините, произошла ошибка при обработке запроса. Попробуйте позже."

async def download_image_base64(file_id: str) -> str:
    file = await bot.get_file(file_id)
    buf = BytesIO()
    await bot.download_file(file.file_path, buf)
    data = buf.getvalue()
    b64 = base64.b64encode(data).decode()
    return f"data:image/jpeg;base64,{b64}"

def make_kb(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Отправить", callback_data=f"send:{ticket_id}"),
        InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit:{ticket_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{ticket_id}"),
    ]])

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

async def send_answer_to_user(user_id: int, answer: str, queue_msg_id: int = None):
    if queue_msg_id:
        try:
            await bot.delete_message(user_id, queue_msg_id)
        except Exception:
            pass

    html = md_to_html(answer)
    parts = split_message(html, MAX_MSG_LEN)
    for part in parts:
        try:
            await bot.send_message(user_id, part, disable_web_page_preview=True)
        except Exception:
            await bot.send_message(user_id, part.replace("<", "&lt;"))

async def auto_send_after_timeout(admin_msg_id: int, delay: int = AUTO_SEND_DELAY):
    await asyncio.sleep(delay)
    if admin_msg_id not in pending:
        return

    d = pending[admin_msg_id]
    user_id = d["user_id"]
    username = d["username"]
    ai_answer = d["ai_answer"]
    ticket_id = d.get("ticket_id", "?")
    queue_msg_id = d.get("queue_msg_id")

    del pending[admin_msg_id]
    edit_mode.pop(admin_msg_id, None)
    active_users.pop(user_id, None)

    try:
        await send_answer_to_user(user_id, ai_answer, queue_msg_id)
        await bot.send_message(ADMIN_ID, f"🤖 <b>Автоотправка</b> заявки #{ticket_id} → @{username}")
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"❌ Автоотправка #{ticket_id}: {e}")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "👋 <b>Привет, админ!</b>\n\n"
            f"⏱ На правку — <b>{AUTO_SEND_DELAY} сек</b>.\n\n"
            "<b>Команды:</b>\n"
            "/list — заявки\n"
            "/reply &lt;id&gt; &lt;текст&gt;\n"
            "/edit &lt;id&gt;\n"
            "/cancel &lt;id&gt;\n"
            "/reset — сброс очереди\n"
            "/clearall — очистить историю"
        )
        return

    await message.answer(
        "👋 <b>Привет! Я DeepSeek AI</b> — твой умный помощник.\n\n"
        "Могу:\n"
        "• Отвечать на вопросы\n"
        "• Анализировать фото 🖼\n"
        "• Писать тексты и код\n"
        "• Переводить\n\n"
        "Просто напиши или отправь фото 👇"
    )

@dp.message(Command("list"), F.from_user.id == ADMIN_ID)
async def cmd_list(message: types.Message):
    if not pending:
        await message.answer("📭 Активных заявок нет")
        return
    lines = ["📋 <b>Активные заявки:</b>\n"]
    for mid, d in pending.items():
        lines.append(f"🆔 #{d.get('ticket_id', '?')} — @{d['username']}\n   💬 {d['text'][:60]}\n")
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

    await send_answer_to_user(d["user_id"], args[2], d.get("queue_msg_id"))
    await message.answer(f"✅ Отправлено @{d['username']}")
    del pending[mid]
    edit_mode.pop(mid, None)
    active_users.pop(d["user_id"], None)

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
        f"✏️ <b>Редактирование #{ticket_id}</b>\n\n"
        f"Отправь новый текст <b>reply</b> на это сообщение."
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

    if d.get("queue_msg_id"):
        try:
            await bot.delete_message(d["user_id"], d["queue_msg_id"])
        except Exception:
            pass

    del pending[mid]
    edit_mode.pop(mid, None)
    active_users.pop(d["user_id"], None)
    await message.answer(f"❌ Заявка #{ticket_id} отменена")

@dp.message(Command("reset"), F.from_user.id == ADMIN_ID)
async def cmd_reset(message: types.Message):
    queue_counter[0] = 1
    active_users.clear()
    await message.answer("🔄 Очередь сброшена")

@dp.message(Command("clearall"), F.from_user.id == ADMIN_ID)
async def cmd_clearall(message: types.Message):
    user_dialogs.clear()
    await message.answer("🧹 История очищена")

@dp.message(Command("queue"), F.from_user.id != ADMIN_ID)
async def user_queue(message: types.Message):
    uid = message.from_user.id
    if uid not in active_users:
        await message.answer("📭 Вы не в очереди.")
        return
    await message.answer(f"🎫 Ваша позиция: #{active_users[uid]}")

@dp.message(Command("cancel"), F.from_user.id != ADMIN_ID)
async def user_cancel(message: types.Message):
    uid = message.from_user.id
    if uid in active_users:
        active_users.pop(uid, None)
        await message.answer("✅ Запрос отменён.")
    else:
        await message.answer("📭 У вас нет активных запросов.")

@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message, F.text)
async def admin_reply_handler(message: types.Message):
    reply_to_id = message.reply_to_message.message_id
    mid, d = find_ticket_by_msg(reply_to_id)

    if d is None:
        active_edits = [m for m, v in edit_mode.items() if v and m in pending]
        if len(active_edits) == 1:
            mid = active_edits[0]
            d = pending[mid]

    if d is None:
        return

    ticket_id = d.get("ticket_id", "?")
    await send_answer_to_user(d["user_id"], message.text, d.get("queue_msg_id"))
    await message.answer(f"✅ Заявка #{ticket_id} отправлена @{d['username']}")
    del pending[mid]
    edit_mode.pop(mid, None)
    active_users.pop(d["user_id"], None)

@dp.message(F.from_user.id != ADMIN_ID)
async def handle_user(message: types.Message):
    user = message.from_user
    if message.text and message.text.startswith("/"):
        return

    text = message.text or message.caption or ""
    image_b64 = None

    if message.photo:
        try:
            photo = message.photo[-1]
            image_b64 = await download_image_base64(photo.file_id)
        except Exception as e:
            logging.error(f"Photo error: {e}")
            await message.answer("⚠️ Не удалось обработать фото.")
            return

    if not text and not image_b64:
        return

    queue_msg = await message.answer(get_queue_text(user.id))

    if not image_b64 and is_creator_question(text):
        ai_answer = get_creator_answer(text)
    else:
        ai_answer = await ask_ai(user.id, text, image_b64)

    ticket_id = next_id[0]
    next_id[0] += 1

    preview = text[:300] if text else "(фото)"
    if image_b64:
        preview = f"🖼 [ФОТО]\n{preview}"

    header = (
        f"🆔 <b>Заявка #{ticket_id}</b>\n\n"
        f"👤 <b>От:</b> {user.full_name} (@{user.username or 'без_ника'})\n"
        f"🆔 <code>{user.id}</code>\n"
        f"🎫 <b>Позиция:</b> #{active_users.get(user.id, '?')}\n\n"
        f"💬 <b>Вопрос:</b>\n{preview}\n\n"
        f"🤖 <b>Ответ ИИ:</b>\n{md_to_html(ai_answer[:1500])}"
    )

    sent = await bot.send_message(
        ADMIN_ID, header, reply_markup=make_kb(ticket_id),
        disable_web_page_preview=True
    )

    pending[sent.message_id] = {
        "user_id": user.id,
        "username": user.username or "без_ника",
        "text": text or "(фото)",
        "ai_answer": ai_answer,
        "ticket_id": ticket_id,
        "last_msg_id": sent.message_id,
        "notice_id": None,
        "queue_msg_id": queue_msg.message_id,
    }

    asyncio.create_task(auto_send_after_timeout(sent.message_id, delay=AUTO_SEND_DELAY))

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
            await send_answer_to_user(d["user_id"], d["ai_answer"], d.get("queue_msg_id"))
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
            f"Отправь новый текст <b>reply</b> на это сообщение.\n"
            f"Уйдёт @{d['username']}.\n\n"
            f"⚠️ Автоотправка через {AUTO_SEND_DELAY} сек."
        )
        d["notice_id"] = notice.message_id

    elif action == "reject":
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Отклонено")
        if d.get("queue_msg_id"):
            try:
                await bot.delete_message(d["user_id"], d["queue_msg_id"])
            except Exception:
                pass
        await call.message.answer(f"❌ Заявка #{ticket_id} отклонена")
        del pending[mid]
        edit_mode.pop(mid, None)
        active_users.pop(d["user_id"], None)

async def main():
    print("🤖 Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
