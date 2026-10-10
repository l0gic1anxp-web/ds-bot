import asyncio
import random
import re
import logging
from collections import deque

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
pending = {}
user_dialogs = {}
edit_mode = {}
next_id = [1]

queue_counter = [1]
active_users = {}

group_history = {}
GROUP_HISTORY_SIZE = 15

AUTO_SEND_DELAY = 15
MAX_MSG_LEN = 4000


# ===== УПОМИНАНИЯ БОТА =====
MENTION_PATTERNS = [
    r"@DeepSeekAI_dsbot",
    r"\bDeepSeek\b",
    r"\bДипсик\b",
    r"\bдипсик\b",
    r"\bдеепсик\b",
    r"\bDeep\s*Seek\b",
    r"\bдип\s*сик\b",
    r"@deepseekai_dsbot",
]


def is_bot_mentioned(text: str) -> bool:
    if not text:
        return False
    for pattern in MENTION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True
    return False


def clean_mention(text: str) -> str:
    cleaned = re.sub(r"@DeepSeekAI_dsbot", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bDeepSeek\b[,:]?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bДипсик\b[,:]?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bдеепсик\b[,:]?\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip() or text


# ===== СИСТЕМНЫЙ ПРОМПТ =====
SYSTEM_PROMPT = """Ты — DeepSeek AI, продвинутый искусственный интеллект нового поколения.

## КАК ОТВЕЧАЕШЬ
Структура: введение → раскрытие → вывод.
Формат: **жирный**, *курсив*, `код`, ```блоки кода```, списки, ## заголовки, умеренные эмодзи.
Стиль: грамотный русский, точный, живой, без воды.

## ПРИНЦИПЫ
- Честность: не знаешь — скажи.
- Точность: факты только если уверен.
- Полезность и глубина.
- Краткость, если можно короче.
- Уважение.

## ЧЕГО НЕ ДЕЛАЕШЬ
- Мед/юр/фин советы как профессионал.
- Незаконное, оскорбления, NSFW, экстремизм.
- Личные данные других.

## ОСОБОЕ
- Признаёшь что ИИ, если спросят.
- Не притворяешься человеком.
- Непонятен вопрос — уточни.

Помогай с текстами, кодом, переводами, объяснениями."""


# ===== УТИЛИТЫ =====
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
        return "⚙️ <b>Обрабатываю ваш запрос...</b>\n\nЭто может занять несколько секунд."

    mins = max(1, round(pos * 0.4 + random.uniform(-0.3, 1.2)))

    return (
        "💭 <b>Ваш запрос принят в обработку</b>\n\n"
        f"🎫 <b>Позиция в очереди:</b> #{pos}\n"
        f"⏱ <b>Ожидаемое время:</b> ~{mins} мин.\n\n"
        "Спасибо за терпение 🙏"
    )


async def ask_ai(user_id: int, text: str, is_group: bool = False, chat_id: int = None):
    """Возвращает ответ ИИ или None при ошибке."""
    if is_group and chat_id is not None:
        if chat_id not in group_history:
            group_history[chat_id] = deque(maxlen=GROUP_HISTORY_SIZE)

        history = list(group_history[chat_id])
        history.append({"role": "user", "content": text})

        try:
            resp = await ai_client.chat.completions.create(
                model=AI_MODEL,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
                temperature=0.9,
                max_tokens=1500,
            )
            answer = resp.choices[0].message.content
            group_history[chat_id].append({"role": "user", "content": text})
            group_history[chat_id].append({"role": "assistant", "content": answer})
            return answer
        except Exception as e:
            logging.error(f"AI error (group): {e}")
            return None
    else:
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
            return None


def make_kb(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Отправить", callback_data=f"send:{ticket_id}"),
            InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit:{ticket_id}"),
        ],
        [
            InlineKeyboardButton(text="🔄 Ещё вариант", callback_data=f"regen:{ticket_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{ticket_id}"),
        ],
    ])


def make_user_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❓ Помощь", callback_data="user:help"),
         InlineKeyboardButton(text="ℹ️ О боте", callback_data="user:about")],
        [InlineKeyboardButton(text="🧹 Очистить историю", callback_data="user:clear")],
    ])


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
    try:
        await bot.send_chat_action(user_id, "typing")
    except Exception:
        pass

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


# ===== START =====
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.from_user.id

    if message.chat.type == "private":
        if uid == ADMIN_ID:
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
            "• Писать тексты и код\n"
            "• Переводить\n"
            "• Объяснять сложное\n\n"
            "⚠️ <i>Фото не поддерживаются — только текст.</i>",
            reply_markup=make_user_kb()
        )
        return

    await message.answer(
        "👋 Привет! Я DeepSeek AI.\n\n"
        "Упомяни меня (<b>DeepSeek</b> или <b>Дипсик</b>) — и я отвечу."
    )


# ===== HELP / ABOUT / CLEAR =====
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📚 <b>Что я умею</b>\n\n"
        "• Отвечать на любые вопросы\n"
        "• Писать и объяснять код\n"
        "• Переводить на разные языки\n"
        "• Помогать с текстами\n"
        "• Объяснять сложные вещи\n\n"
        "<b>В группе:</b> упомяни меня — <code>DeepSeek</code>, <code>Дипсик</code> или <code>@DeepSeekAI_dsbot</code>.",
        reply_markup=make_user_kb()
    )


@dp.message(Command("about"))
async def cmd_about(message: types.Message):
    await message.answer(
        "🤖 <b>DeepSeek AI Bot</b>\n\n"
        "Продвинутый ИИ-ассистент в Telegram.\n"
        "Работает на базе языковой модели DeepSeek.\n\n"
        "Версия 2.0",
        reply_markup=make_user_kb()
    )


@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    uid = message.from_user.id
    user_dialogs.pop(uid, None)
    await message.answer("🧹 История очищена. Можем начать заново!")


# ===== КНОПКИ ЮЗЕРА =====
@dp.callback_query(F.data.startswith("user:"))
async def user_callback(call: CallbackQuery):
    action = call.data.split(":")[1]
    if action == "help":
        await call.message.edit_text(
            "📚 <b>Что я умею</b>\n\n"
            "• Отвечать на вопросы\n"
            "• Писать код\n"
            "• Переводить\n"
            "• Объяснять\n\n"
            "<b>В группе:</b> упомяни <code>DeepSeek</code> или <code>Дипсик</code>.",
            reply_markup=make_user_kb()
        )
    elif action == "about":
        await call.message.edit_text(
            "🤖 <b>DeepSeek AI Bot</b>\n\nПродвинутый ИИ-ассистент.\nВерсия 2.0",
            reply_markup=make_user_kb()
        )
    elif action == "clear":
        uid = call.from_user.id
        user_dialogs.pop(uid, None)
        await call.answer("История очищена")
        await call.message.edit_text("🧹 История очищена. Напиши новый вопрос!", reply_markup=make_user_kb())
    else:
        await call.answer()


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
    group_history.clear()
    await message.answer("🧹 Вся история очищена")


# ===== РЕДАКТИРОВАНИЕ REPLY =====
@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message, F.text, F.chat.type == "private")
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


# ===== ГРУППА =====
@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group(message: types.Message):
    if not message.text:
        return

    text = message.text
    chat_id = message.chat.id

    if chat_id not in group_history:
        group_history[chat_id] = deque(maxlen=GROUP_HISTORY_SIZE)

    user_name = message.from_user.full_name if message.from_user else "Кто-то"
    group_history[chat_id].append({
        "role": "user",
        "content": f"[{user_name}]: {text}"
    })

    if not is_bot_mentioned(text):
        return

    question = clean_mention(text)
    if not question:
        question = text

    try:
        await bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass

    answer = await ask_ai(0, question, is_group=True, chat_id=chat_id)

    if not answer:
        return

    html = md_to_html(answer)
    parts = split_message(html, MAX_MSG_LEN)

    for i, part in enumerate(parts):
        try:
            if i == 0:
                await message.reply(part, disable_web_page_preview=True)
            else:
                await bot.send_message(chat_id, part, disable_web_page_preview=True)
        except Exception:
            await bot.send_message(chat_id, part.replace("<", "&lt;"))


# ===== ЛИЧКА =====
@dp.message(F.chat.type == "private", F.from_user.id != ADMIN_ID)
async def handle_user(message: types.Message):
    user = message.from_user
    if message.text and message.text.startswith("/"):
        return

    text = message.text or message.caption or ""

    if message.photo:
        await message.answer("⚠️ <b>Обработка изображений недоступна.</b>\n\nОпишите вопрос текстом.")
        return

    if not text:
        return

    queue_msg = await message.answer(get_queue_text(user.id))

    try:
        await bot.send_chat_action(user.id, "typing")
    except Exception:
        pass

    ai_answer = await ask_ai(user.id, text)

    if not ai_answer:
        # Убираем очередь и молчим
        try:
            await bot.delete_message(user.id, queue_msg.message_id)
        except Exception:
            pass
        return

    ticket_id = next_id[0]
    next_id[0] += 1

    preview = text[:300]

    header = (
        f"🆔 <b>Заявка #{ticket_id}</b>\n\n"
        f"👤 <b>От:</b> {user.full_name} (@{user.username or 'без_ника'})\n"
        f"🆔 <code>{user.id}</code>\n"
        f"🎫 <b>Позиция:</b> #{active_users.get(user.id, '?')}\n\n"
        f"💬 <b>Вопрос:</b>\n{preview}\n\n"
        f"🤖 <b>Ответ ИИ:</b>\n{md_to_html(ai_answer[:2000])}"
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
        "queue_msg_id": queue_msg.message_id,
    }

    asyncio.create_task(auto_send_after_timeout(sent.message_id, delay=AUTO_SEND_DELAY))


# ===== КНОПКИ АДМИНА =====
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
            f"Отправь новый текст <b>reply</b> на это сообщение."
        )
        d["notice_id"] = notice.message_id

    elif action == "regen":
        await call.answer("Генерирую новый вариант...")
        try:
            new_answer = await ask_ai(d["user_id"], d["text"])
            if not new_answer:
                await call.message.answer("⚠️ Не удалось получить новый ответ")
                return
            d["ai_answer"] = new_answer
            header = (
                f"🆔 <b>Заявка #{ticket_id}</b> (перегенерировано)\n\n"
                f"👤 <b>От:</b> {d['username']}\n\n"
                f"💬 <b>Вопрос:</b>\n{d['text'][:300]}\n\n"
                f"🤖 <b>Новый ответ:</b>\n{md_to_html(new_answer[:2000])}"
            )
            sent2 = await call.message.answer(header, reply_markup=make_kb(ticket_id))
            d["last_msg_id"] = sent2.message_id
            asyncio.create_task(auto_send_after_timeout(mid, delay=AUTO_SEND_DELAY))
        except Exception as e:
            await call.message.answer(f"❌ Ошибка: {e}")

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
