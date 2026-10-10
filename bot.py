import asyncio
import random
import re
import logging
from io import BytesIO

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
pending = {}          # admin_msg_id -> {user_id, username, text, ai_answer, ticket_id, last_msg_id, notice_id, queue_msg_id, user_msg_id}
user_dialogs = {}     # user_id -> история [{role, content}]
edit_mode = {}
next_id = [1]

# ===== ОЧЕРЕДЬ =====
queue_counter = [1]
active_users = {}

# ===== НАСТРОЙКИ =====
AUTO_SEND_DELAY = 15   # сек на правку
MAX_MSG_LEN = 4000     # макс длина одного сообщения


SYSTEM_PROMPT = """Ты — DeepSeek AI, умный, грамотный и дружелюбный ассистент.
Отвечай на русском языке (или на языке пользователя) структурированно и по делу.

ПРАВИЛА:
1. Отвечай чётко, грамотно, без воды.
2. Используй markdown: **жирный**, *курсив*, `код`, ```блоки кода```.
3. Если вопрос сложный — структурируй ответ списками и заголовками.
4. Если видишь фото — опиши что на нём, ответь на вопрос пользователя по фото.
5. Если можешь ошибиться — предупреди об этом.
6. Не повторяйся, каждый ответ формулируй по-новому.
7. Будь вежливым, но не подхалимствуй.
8. Если вопрос непонятен — задай уточняющий вопрос.

Ты DeepSeek AI — помогай с текстами, кодом, переводом, анализом, объяснениями."""


# ===== УТИЛИТЫ =====
def md_to_html(text: str) -> str:
    """Конвертирует markdown в HTML для Telegram."""
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
    """Разбивает длинный текст на части."""
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
        return "💭 <b>Ваш запрос обрабатывается...</b>\n\nПожалуйста, подождите."

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
        "⏳ <b>Ваш запрос в обработке</b>\n\n"
        f"📊 <b>Статус серверов:</b> {status} ({load}%)\n"
        f"👥 <b>Пользователей онлайн:</b> {online}\n"
        f"🎫 <b>Ваша позиция в очереди:</b> #{pos}\n"
        f"⏱ <b>Примерное ожидание:</b> ~{mins} мин.\n\n"
        "Спасибо за понимание 🙏"
    )


async def ask_ai(user_id: int, text: str, image_url: str = None) -> str:
    """Отправляет запрос в ИИ. Поддерживает текст, фото или и то и то."""
    history = user_dialogs.get(user_id, [])

    # Формируем контент
    if image_url:
        # Vision-запрос
        content = []
        if text:
            content.append({"type": "text", "text": text})
        else:
            content.append({"type": "text", "text": "Что на этом изображении? Опиши подробно."})
        content.append({"type": "image_url", "image_url": {"url": image_url}})
        user_msg = {"role": "user", "content": content}
    else:
        user_msg = {"role": "user", "content": text}

    history.append(user_msg)
    history = history[-10:]  # последние 10 сообщений

    try:
        resp = await ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
            temperature=1.0,
            max_tokens=1500,
        )
        answer = resp.choices[0].message.content

        # Для истории сохраняем упрощённо
        history.append({"role": "assistant", "content": answer})
        user_dialogs[user_id] = history
        return answer
    except Exception as e:
        logging.error(f"AI error: {e}")
        return "⚠️ Извините, произошла ошибка при обработке запроса. Попробуйте позже."


async def download_image(file_id: str) -> str:
    """Скачивает фото и возвращает base64 data URL."""
    import base64
    file = await bot.get_file(file_id)
    file_bytes = BytesIO()
    await bot.download_file(file.file_path, file_bytes)
    data = file_bytes.getvalue()
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
    """Отправляет ответ юзеру с удалением очереди."""
    # Удаляем сообщение об очереди
    if queue_msg_id:
        try:
            await bot.delete_message(user_id, queue_msg_id)
        except Exception:
            pass

    # Конвертируем и разбиваем
    html = md_to_html(answer)
    parts = split_message(html, MAX_MSG_LEN)

    for i, part in enumerate(parts):
        try:
            await bot.send_message(user_id, part, disable_web_page_preview=True)
        except Exception as e:
            logging.error(f"Send error: {e}")
            # Если HTML сломался — шлём без разметки
            await bot.send_message(user_id, part.replace("<", "&lt;"))


async def auto_send_after_timeout(admin_msg_id: int, delay: int = AUTO_SEND_DELAY):
    """Если ты не ответил за N секунд — бот сам отправляет."""
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
        await bot.send_message(
            ADMIN_ID,
            f"🤖 <b>Автоотправка</b> заявки #{ticket_id} → @{username}"
        )
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"❌ Автоотправка #{ticket_id}: {e}")


# ===== START =====
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "👋 <b>Привет, админ!</b>\n\n"
            f"⏱ На правку заявки — <b>{AUTO_SEND_DELAY} сек</b>. Не успел → автоотправка.\n\n"
            "<b>Команды:</b>\n"
            "/list — активные заявки\n"
            "/reply &lt;id&gt; &lt;текст&gt;\n"
            "/edit &lt;id&gt;\n"
            "/cancel &lt;id&gt;\n"
            "/reset — сброс очереди\n"
            "/clear — очистить историю юзера\n"
            "/clearall — очистить всю историю"
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


# ===== КОМАНДЫ АДМИНА =====
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

    text = args[2]
    queue_msg_id = d.get("queue_msg_id")
    await send_answer_to_user(d["user_id"], text, queue_msg_id)
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
        f"✏️ <b>Режим редактирования #{ticket_id}</b>\n\n"
        f"Отправь новый текст <b>reply (свайп влево)</b> на <b>это сообщение</b>.\n"
        f"Уйдёт @{d['username']}."
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

    # Удаляем сообщение очереди у юзера
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


@dp.message(Command("clear"), F.from_user.id == ADMIN_ID)
async def cmd_clear(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: <code>/clear &lt;user_id&gt;</code>")
        return
    try:
        uid = int(args[1])
    except ValueError:
        await message.answer("❌ ID должен быть числом")
        return
    user_dialogs.pop(uid, None)
    await message.answer(f"🧹 История {uid} очищена")


@dp.message(Command("clearall"), F.from_user.id == ADMIN_ID)
async def cmd_clearall(message: types.Message):
    user_dialogs.clear()
    await message.answer("🧹 Вся история диалогов очищена")


# ===== КОМАНДЫ ЮЗЕРА =====
@dp.message(Command("queue"), F.from_user.id != ADMIN_ID)
async def user_queue(message: types.Message):
    uid = message.from_user.id
    if uid not in active_users:
        await message.answer("📭 Вы не в очереди.")
        return
    pos = active_users[uid]
    await message.answer(f"🎫 Ваша позиция в очереди: #{pos}")


@dp.message(Command("cancel"), F.from_user.id != ADMIN_ID)
async def user_cancel(message: types.Message):
    uid = message.from_user.id
    if uid in active_users:
        active_users.pop(uid, None)
        await message.answer("✅ Ваш запрос отменён.")
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
    queue_msg_id = d.get("queue_msg_id")

    await send_answer_to_user(d["user_id"], new_text, queue_msg_id)
    await message.answer(f"✅ Заявка #{ticket_id} отправлена @{d['username']}")
    del pending[mid]
    edit_mode.pop(mid, None)
    active_users.pop(d["user_id"], None)


# ===== СООБЩЕНИЕ ОТ ДРУГА =====
@dp.message(F.from_user.id != ADMIN_ID)
async def handle_user(message: types.Message):
    user = message.from_user

    if message.text and message.text.startswith("/"):
        return

    text = message.text or message.caption or ""
    image_url = None
    user_msg_id = message.message_id

    # Если есть фото — скачиваем
    if message.photo:
        try:
            photo = message.photo[-1]  # самое большое
            image_url = await download_image(photo.file_id)
        except Exception as e:
            logging.error(f"Photo download error: {e}")
            await message.answer("⚠️ Не удалось обработать фото. Опишите текстом.")
            return

    if not text and not image_url:
        return

    # Отправляем сообщение об очереди/обработке
    queue_msg = await message.answer(get_queue_text(user.id))

    # Генерим ответ ИИ
    ai_answer = await ask_ai(user.id, text, image_url)

    ticket_id = next_id[0]
    next_id[0] += 1

    # Формируем превью для админа
    preview = text[:300] if text else "(фото)"
    if image_url:
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
        "user_msg_id": user_msg_id,
    }

    # Автоотправка через 15 секунд
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
        queue_msg_id = d.get("queue_msg_id")
        try:
            await send_answer_to_user(d["user_id"], d["ai_answer"], queue_msg_id)
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
            f"⚠️ Автоотправка через {AUTO_SEND_DELAY} сек всё равно сработает."
        )
        d["notice_id"] = notice.message_id

    elif action == "reject":
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Отклонено")

        # Удаляем очередь у юзера
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
