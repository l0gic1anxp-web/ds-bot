import asyncio
import random
import re
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
pending = {}
user_dialogs = {}
edit_mode = {}
next_id = [1]

queue_counter = [1]
active_users = {}

# Настройки юзера: режим, стиль, имя, счётчик
user_modes = {}      # user_id -> "fast" | "think"
user_styles = {}     # user_id -> "default" | "official" | "friendly" | "brief"
user_names = {}      # user_id -> имя
user_counts = {}     # user_id -> сколько запросов

AUTO_SEND_DELAY = 15
MAX_MSG_LEN = 4000


# ===== СТИЛИ ОТВЕТОВ =====
STYLE_HINTS = {
    "default": "",
    "official": "\n\nСТИЛЬ: Официальный, деловой. Без эмодзи. Строгая структура.",
    "friendly": "\n\nСТИЛЬ: Дружеский, тёплый. Можно эмодзи. Общайся как хороший друг.",
    "brief": "\n\nСТИЛЬ: Максимально кратко. Только суть. 1-3 предложения.",
}


# ===== ПРОДВИНУТЫЙ ПРОМПТ (быстрый режим) =====
SYSTEM_PROMPT_FAST = """Ты — DeepSeek AI, продвинутый искусственный интеллект нового поколения, созданный компанией DeepSeek (Китай). Работаешь в Telegram-боте пользователя Логикан.

## КТО ТЫ
Ты — настоящий ИИ. Языковая модель. Умеешь рассуждать, анализировать.

ПРО СОЗДАТЕЛЯ:
- Компания DeepSeek — создатель тебя как ИИ.
- Логикан — создатель ЭТОГО Telegram-бота.
- Различай эти вопросы!

## КАК ОТВЕЧАЕШЬ
Структура:
- Введение (1-2 предложения — суть).
- Раскрытие (списки, подзаголовки).
- Вывод.

Формат:
- **жирный** для ключевых слов
- *курсив* для акцентов
- `код` для терминов
- ```блоки``` для кода
- Списки для перечислений
- ## заголовки для разделов
- Эмодзи умеренно

Стиль: грамотный, точный, живой, без воды.

## ПРИНЦИПЫ
Честность, точность, полезность, глубина, краткость, уважение.

## ЧЕГО НЕ ДЕЛАЕШЬ
- Мед/юр/фин советы как профессионал.
- Незаконное, оскорбления, NSFW.
- Личные данные других.

## ОСОБОЕ
- Признаёшь что ИИ.
- Не притворяешься человеком.
- Непонятен вопрос — уточни.

Ты — лучший собеседник в Telegram."""


# ===== ПРОДВИНУТЫЙ ПРОМПТ (режим рассуждения) =====
SYSTEM_PROMPT_THINK = """Ты — DeepSeek AI в режиме глубокого рассуждения (Reasoning Mode). Создан компанией DeepSeek (Китай). Работаешь в Telegram-боте Логикана.

## КТО ТЫ
Настоящий ИИ. Языковая модель. Умеешь думать.

ПРО СОЗДАТЕЛЯ:
- Компания DeepSeek — создатель тебя как ИИ.
- Логикан — создатель ЭТОГО бота.

## РЕЖИМ РАССУЖДЕНИЯ

ФОРМАТ ОТВЕТА (обязательно!):

<reasoning>
Твои размышления: анализ вопроса, разбор, план ответа. 3-7 предложений или пункты.
</reasoning>

Основной ответ с markdown.

ВАЖНО:
- Тег <reasoning> обязателен.
- В рассуждении — реальный ход мыслей.
- Если вопрос простой — рассуждение короткое.

## КАК ОТВЕЧАЕШЬ
Структура: введение → раскрытие (списки, подзаголовки) → вывод.
Формат: **жирный**, *курсив*, `код`, ```блоки```, списки, ## заголовки, умеренные эмодзи.
Стиль: грамотный, точный, живой.

## ПРИНЦИПЫ
Честность, точность, полезность, глубина, краткость, уважение.

## ЧЕГО НЕ ДЕЛАЕШЬ
- Мед/юр/фин советы.
- Незаконное, оскорбления, NSFW.
- Личные данные.

## ОСОБОЕ
- Признаёшь что ИИ.
- Непонятен вопрос — уточни.

Думай глубоко. Отвечай красиво."""


# ===== ОТВЕТЫ ПРО СОЗДАТЕЛЯ =====
CREATOR_ANSWERS = {
    "кто создал бота": "Этого бота в Telegram создал Логикан.",
    "кто создатель бота": "Этого бота в Telegram создал Логикан.",
    "кто автор бота": "Автор этого бота — Логикан.",
    "кто разработчик бота": "Разработчик бота — Логикан.",
    "создатель бота": "Создатель этого бота — Логикан.",
    "кто создал deepseek": "DeepSeek создала одноимённая компания из Китая.",
    "кто создал дипсик": "DeepSeek создала одноимённая компания из Китая.",
    "кто создал тебя": "Меня как ИИ создала компания DeepSeek. Этого бота — Логикан.",
    "кто тебя создал": "Меня как ИИ создала компания DeepSeek. Этого бота — Логикан.",
    "кто твой создатель": "Как ИИ — компания DeepSeek. Как бота — Логикан.",
}

CREATOR_PATTERNS = [
    r"кто\s+создал\s+(этого\s+)?бота",
    r"кто\s+(создатель|автор|разработчик|владелец|хозяин)\s+бота",
    r"создатель\s+бота",
    r"кто\s+создал\s+(deepseek|дипсик)",
    r"кто\s+создал\s+тебя",
    r"кто\s+тебя\s+(создал|сделал|придумал|написал)",
    r"кто\s+(твой|ваш)\s+создатель",
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
    return "Компания DeepSeek создала меня как ИИ. Этого бота в Telegram запустил Логикан."


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


def split_reasoning(text: str):
    match = re.search(r"<reasoning>(.*?)</reasoning>", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        reasoning = match.group(1).strip()
        answer = text.replace(match.group(0), "").strip()
        return reasoning, answer
    return None, text.strip()


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
        "Мы сообщим, как только ответ будет готов. Спасибо за терпение 🙏"
    )


async def ask_ai(user_id: int, text: str) -> str:
    history = user_dialogs.get(user_id, [])
    history.append({"role": "user", "content": text})
    history = history[-10:]

    mode = user_modes.get(user_id, "think")
    style = user_styles.get(user_id, "default")
    sys_prompt = (SYSTEM_PROMPT_THINK if mode == "think" else SYSTEM_PROMPT_FAST) + STYLE_HINTS.get(style, "")

    # Персонализация
    name = user_names.get(user_id)
    if name:
        sys_prompt += f"\n\nОбращайся к пользователю по имени: {name}."

    try:
        resp = await ai_client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "system", "content": sys_prompt}] + history,
            temperature=0.85 if mode == "think" else 1.0,
            max_tokens=2000,
        )
        answer = resp.choices[0].message.content
        history.append({"role": "assistant", "content": answer})
        user_dialogs[user_id] = history
        return answer
    except Exception as e:
        logging.error(f"AI error: {e}")
        return "⚠️ Извините, произошла ошибка. Попробуйте позже."


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
    # Убираем "печатает..."
    try:
        await bot.send_chat_action(user_id, "typing")
    except Exception:
        pass

    if queue_msg_id:
        try:
            await bot.delete_message(user_id, queue_msg_id)
        except Exception:
            pass

    reasoning, main_answer = split_reasoning(answer)

    if reasoning:
        reasoning_html = md_to_html(reasoning)
        quote_text = f"<blockquote>🧠 <b>Размышления:</b>\n\n{reasoning_html}</blockquote>"
        for part in split_message(quote_text, MAX_MSG_LEN):
            try:
                await bot.send_message(user_id, part, disable_web_page_preview=True)
            except Exception:
                await bot.send_message(user_id, part.replace("<", "&lt;"))

    html = md_to_html(main_answer)
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

    mode = user_modes.get(uid, "think")
    style = user_styles.get(uid, "default")
    name = user_names.get(uid, "")
    count = user_counts.get(uid, 0)

    mode_name = "🧠 С рассуждением" if mode == "think" else "⚡ Без рассуждения"
    style_name = {"default": "обычный", "official": "официальный", "friendly": "дружеский", "brief": "краткий"}[style]
    greeting = f", <b>{name}</b>" if name else ""

    await message.answer(
        f"👋 <b>Привет{greeting}! Я DeepSeek AI</b> — продвинутый ИИ.\n\n"
        "Умею:\n"
        "• Отвечать на вопросы любой сложности\n"
        "• Писать и объяснять код\n"
        "• Помогать с текстами и переводами\n"
        "• Думать и рассуждать\n\n"
        f"<b>Режим:</b> {mode_name}\n"
        f"<b>Стиль:</b> {style_name}\n"
        f"<b>Запросов сделано:</b> {count}\n\n"
        "Команды: /mode /style /name /help /about\n\n"
        "⚠️ <i>Обработка изображений недоступна — только текст.</i>"
    )


# ===== HELP / ABOUT =====
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📚 <b>Команды бота</b>\n\n"
        "/mode — режим ответа (с рассуждением / без)\n"
        "/style — стиль ответа (обычный / официальный / дружеский / краткий)\n"
        "/name — задать имя, чтобы бот обращался по имени\n"
        "/mystats — сколько запросов сделал\n"
        "/queue — позиция в очереди\n"
        "/cancel — отменить запрос\n"
        "/help — эта справка\n"
        "/about — о боте\n\n"
        "Просто напиши вопрос — и я отвечу!"
    )


@dp.message(Command("about"))
async def cmd_about(message: types.Message):
    await message.answer(
        "🤖 <b>DeepSeek AI Bot</b>\n\n"
        "Продвинутый ИИ-ассистент в Telegram.\n"
        "Работает на базе языковой модели DeepSeek.\n\n"
        "<b>Создатель ИИ:</b> компания DeepSeek (Китай)\n"
        "<b>Создатель этого бота:</b> Логикан\n\n"
        "Версия: 2.0"
    )


@dp.message(Command("mystats"))
async def cmd_mystats(message: types.Message):
    uid = message.from_user.id
    count = user_counts.get(uid, 0)
    await message.answer(f"📊 Ты сделал <b>{count}</b> запросов.")


@dp.message(Command("name"))
async def cmd_name(message: types.Message):
    args = message.text.split(maxsplit=1)
    uid = message.from_user.id
    if len(args) < 2:
        current = user_names.get(uid, "не задано")
        await message.answer(f"Твоё имя: <b>{current}</b>\n\nЧтобы задать: <code>/name Иван</code>")
        return
    name = args[1][:30]
    user_names[uid] = name
    await message.answer(f"✅ Запомнил! Буду обращаться к тебе <b>{name}</b>.")


# ===== РЕЖИМЫ =====
@dp.message(Command("mode"))
async def cmd_mode(message: types.Message):
    uid = message.from_user.id
    current = user_modes.get(uid, "think")
    mode_name = "🧠 С рассуждением" if current == "think" else "⚡ Без рассуждения"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧠 С рассуждением", callback_data="mode:think")],
        [InlineKeyboardButton(text="⚡ Без рассуждения", callback_data="mode:fast")],
    ])

    await message.answer(
        f"<b>Текущий режим:</b> {mode_name}\n\n"
        "🧠 <b>С рассуждением</b> — ИИ думает, показывает ход мыслей в цитате. Умнее.\n\n"
        "⚡ <b>Без рассуждения</b> — быстрый ответ. Проще.",
        reply_markup=kb
    )


@dp.callback_query(F.data.startswith("mode:"))
async def mode_callback(call: CallbackQuery):
    mode = call.data.split(":")[1]
    user_modes[call.from_user.id] = mode
    name = "🧠 С рассуждением" if mode == "think" else "⚡ Без рассуждения"
    await call.answer(f"Режим: {name}")
    await call.message.edit_text(f"✅ Режим: <b>{name}</b>")


# ===== СТИЛИ =====
@dp.message(Command("style"))
async def cmd_style(message: types.Message):
    uid = message.from_user.id
    current = user_styles.get(uid, "default")
    names = {"default": "Обычный", "official": "Официальный", "friendly": "Дружеский", "brief": "Краткий"}

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обычный", callback_data="style:default")],
        [InlineKeyboardButton(text="Официальный", callback_data="style:official")],
        [InlineKeyboardButton(text="Дружеский", callback_data="style:friendly")],
        [InlineKeyboardButton(text="Краткий", callback_data="style:brief")],
    ])

    await message.answer(
        f"<b>Текущий стиль:</b> {names[current]}\n\nВыбери новый:",
        reply_markup=kb
    )


@dp.callback_query(F.data.startswith("style:"))
async def style_callback(call: CallbackQuery):
    style = call.data.split(":")[1]
    user_styles[call.from_user.id] = style
    names = {"default": "Обычный", "official": "Официальный", "friendly": "Дружеский", "brief": "Краткий"}
    await call.answer(f"Стиль: {names[style]}")
    await call.message.edit_text(f"✅ Стиль: <b>{names[style]}</b>")


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
    await message.answer("🧹 История очищена")


# ===== КОМАНДЫ ЮЗЕРА =====
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


# ===== РЕДАКТИРОВАНИЕ REPLY =====
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


# ===== СООБЩЕНИЕ ОТ ЮЗЕРА =====
@dp.message(F.from_user.id != ADMIN_ID)
async def handle_user(message: types.Message):
    user = message.from_user
    if message.text and message.text.startswith("/"):
        return

    text = message.text or message.caption or ""

    if message.photo:
        await message.answer("⚠️ <b>Обработка изображений временно недоступна.</b>\n\nОпишите вопрос текстом.")
        return

    if not text:
        return

    # Счётчик
    user_counts[user.id] = user_counts.get(user.id, 0) + 1

    queue_msg = await message.answer(get_queue_text(user.id))

    # Показываем "печатает..."
    try:
        await bot.send_chat_action(user.id, "typing")
    except Exception:
        pass

    if is_creator_question(text):
        ai_answer = get_creator_answer(text)
    else:
        ai_answer = await ask_ai(user.id, text)

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
            await send_answer_to_user(d["user_id"], d["ai_answer"], d.get("queue_msg_id"))
            await call.message.answer(f"✅ Заявка #{ticket_id} отправлена @{d['username']}")
            del pending[mid]
            edit_mode.pop(mid, None)
            active_users.pop(d["user_id"], None)
        except Exception as e:
            await call.message.answer(f"❌ Ошибка: {e}")

    elif action == "edit":
        await call.answer("Режим редактирования")
        edit
