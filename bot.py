import os
import re
import html
import logging
import requests
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

BASE_URL = "https://vgek43.ru"
DEFAULT_GROUP = "Д-12"

# На хостинге обычно прокси не нужен.
# Если нужен — добавь переменную окружения PROXY_URL.
PROXY_URL = os.getenv("PROXY_URL", "").strip()

REQUEST_PROXIES = None
if PROXY_URL:
    REQUEST_PROXIES = {
        "http": PROXY_URL,
        "https": PROXY_URL,
    }

LAST_MESSAGES_BY_CHAT = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


# ==========================
# КЛАВИАТУРЫ
# ==========================

def main_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📌 Сегодня", callback_data="today"),
                InlineKeyboardButton("➡️ Завтра", callback_data="tomorrow"),
            ],
            [
                InlineKeyboardButton("📅 Неделя", callback_data="week"),
                InlineKeyboardButton("🔄 Обновить", callback_data="refresh"),
            ],
        ]
    )


def week_keyboard(days):
    buttons = []

    row = []

    for day in days:
        date_short = pretty_short_date(day["date"])
        weekday = day["weekday"]

        label = f"{weekday} {date_short}"

        row.append(
            InlineKeyboardButton(
                label,
                callback_data=f"day:{day['date']}"
            )
        )

        if len(row) == 2:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    buttons.append(
        [
            InlineKeyboardButton("📌 Сегодня", callback_data="today"),
            InlineKeyboardButton("➡️ Завтра", callback_data="tomorrow"),
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton("🔄 Обновить", callback_data="refresh"),
        ]
    )

    return InlineKeyboardMarkup(buttons)


def day_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📌 Сегодня", callback_data="today"),
                InlineKeyboardButton("➡️ Завтра", callback_data="tomorrow"),
            ],
            [
                InlineKeyboardButton("📅 Неделя", callback_data="week"),
                InlineKeyboardButton("🔄 Обновить", callback_data="refresh"),
            ],
        ]
    )


# ==========================
# ОТПРАВКА / РЕДАКТИРОВАНИЕ
# ==========================

async def delete_old_bot_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    old_message_ids = LAST_MESSAGES_BY_CHAT.get(chat_id, [])

    for message_id in old_message_ids:
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=message_id
            )
        except Exception:
            pass

    LAST_MESSAGES_BY_CHAT[chat_id] = []


async def delete_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id
        )
    except Exception:
        pass


async def send_new_screen(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
):
    chat_id = update.effective_chat.id

    await delete_old_bot_messages(update, context)
    await delete_user_message(update, context)

    message = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )

    LAST_MESSAGES_BY_CHAT[chat_id] = [message.message_id]

    return message


async def edit_screen(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
):
    query = update.callback_query

    if query:
        try:
            await query.edit_message_text(
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return
        except Exception:
            pass

    await send_new_screen(
        update,
        context,
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


# ==========================
# БАЗОВЫЕ ФУНКЦИИ
# ==========================

def clean_text(text: str) -> str:
    text = html.unescape(str(text))
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def escape_html_text(text) -> str:
    return html.escape(str(text), quote=False)


def fetch_html(url: str) -> str:
    response = requests.get(
        url,
        timeout=30,
        proxies=REQUEST_PROXIES,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    response.raise_for_status()
    response.encoding = "windows-1251"

    return response.text


def normalize_group(value: str) -> str:
    value = str(value).upper()
    value = value.replace(" ", "")
    value = value.replace("–", "-")
    value = value.replace("—", "-")
    return value


def normalize_time(time_text: str) -> str:
    if not time_text:
        return "время не указано"

    return (
        str(time_text)
        .replace(".", ":")
        .replace("-", "–")
        .strip()
    )


def parse_date(date_text: str):
    try:
        return datetime.strptime(date_text, "%d.%m.%Y").date()
    except Exception:
        return None


def now_local_date():
    # Киров / Москва: UTC+3.
    return (datetime.utcnow() + timedelta(hours=3)).date()


def pretty_short_date(date_text: str) -> str:
    parts = date_text.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return date_text


def plural_lessons(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "пара"

    if count % 10 in [2, 3, 4] and count % 100 not in [12, 13, 14]:
        return "пары"

    return "пар"


def is_credit_or_exam(subject: str) -> bool:
    subject_lower = subject.lower()
    return (
        "зач" in subject_lower
        or "экзамен" in subject_lower
    )


def is_consultation(subject: str) -> bool:
    return "консультац" in subject.lower()


def important_prefix(subject: str) -> str:
    if "экзамен" in subject.lower():
        return "🚨 "

    if is_credit_or_exam(subject):
        return "⚠️ "

    if is_consultation(subject):
        return "💬 "

    return ""


def shorten_subject(subject: str) -> str:
    subject = subject.replace("Консультация к экзамену по дисциплине", "Конс.")
    subject = subject.replace("Основы ораторского искусства", "Ораторское")
    subject = subject.replace("История мировой и отечественной культуры", "ИМК")
    subject = subject.replace("Диф. зач.", "Диф.зач.")
    subject = subject.replace("Диф.зач.", "Диф.зач.")
    subject = subject.strip()
    return subject


def parse_time_range(time_text: str):
    if not time_text:
        return None

    normalized = normalize_time(time_text)

    match = re.search(
        r"(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})",
        normalized
    )

    if not match:
        return None

    return match.group(1), match.group(2)


def get_day_time_range(day) -> str:
    starts = []
    ends = []

    for lesson in day.get("lessons", []):
        parsed = parse_time_range(lesson.get("time", ""))

        if not parsed:
            continue

        start, end = parsed
        starts.append(start)
        ends.append(end)

    if not starts or not ends:
        return ""

    return f"{starts[0]}–{ends[-1]}"


def count_day_lessons(day) -> int:
    return len(day.get("lessons", []))


def get_updated_line(schedule_data) -> str:
    updated = schedule_data.get("updated", "")

    if not updated:
        return "🕒 Время обновления не найдено"

    return f"🕒 {escape_html_text(updated)}"


def get_day_label(day) -> str:
    week_type = ""

    if day.get("week_type"):
        week_type = f"-{day['week_type']}"

    return f"{day['weekday']}{week_type}"


# ==========================
# ПОИСК СТРАНИЦЫ ГРУППЫ
# ==========================

def find_group_url(group_name: str):
    groups_url = f"{BASE_URL}/cg.htm"
    page_html = fetch_html(groups_url)

    soup = BeautifulSoup(page_html, "html.parser")
    target = normalize_group(group_name)

    for link in soup.find_all("a"):
        text = clean_text(link.get_text(" ", strip=True))
        href = link.get("href")

        if not text or not href:
            continue

        if normalize_group(text) == target:
            return urljoin(groups_url, href)

    return None


# ==========================
# ПАРСИНГ РАСПИСАНИЯ
# ==========================

def parse_day(text: str):
    match = re.search(r"(\d{2}\.\d{2}\.\d{4})\s+([А-Яа-яЁё]{2})-?(\d+)?", text)

    if not match:
        return None

    return {
        "date": match.group(1),
        "weekday": match.group(2),
        "week_type": match.group(3),
        "lessons": []
    }


def parse_lesson_header(text: str):
    if "Пара" not in text:
        return None

    number_match = re.search(r"(\d+)", text)
    time_match = re.search(r"(\d{1,2}[.:]\d{2}\s*-\s*\d{1,2}[.:]\d{2})", text)

    if not number_match:
        return None

    return {
        "number": number_match.group(1),
        "time": time_match.group(1).replace(" ", "") if time_match else "",
        "items": []
    }


def parse_lesson_info(cell):
    subject_tag = cell.find("a", class_="z1")
    room_tag = cell.find("a", class_="z2")
    teacher_tag = cell.find("a", class_="z3")

    subject = clean_text(subject_tag.get_text(" ", strip=True)) if subject_tag else ""
    room = clean_text(room_tag.get_text(" ", strip=True)) if room_tag else ""
    teacher = clean_text(teacher_tag.get_text(" ", strip=True)) if teacher_tag else ""

    if not subject:
        return None

    return {
        "subject": subject,
        "room": room,
        "teacher": teacher,
    }


def parse_group_schedule(group_name: str):
    group_url = find_group_url(group_name)

    if not group_url:
        raise Exception(f"Не нашёл группу {group_name} на странице групп.")

    page_html = fetch_html(group_url)
    soup = BeautifulSoup(page_html, "html.parser")

    table = soup.find("table", class_="inf")

    if not table:
        raise Exception("Не нашёл таблицу расписания.")

    schedule = []
    current_day = None

    for row in table.find_all("tr"):
        cells = row.find_all("td")

        if not cells:
            continue

        first_text = clean_text(cells[0].get_text(" ", strip=True))

        if re.search(r"\d{2}\.\d{2}\.\d{4}", first_text):
            current_day = parse_day(first_text)

            if current_day:
                schedule.append(current_day)

            lesson_cell_index = 1
        else:
            lesson_cell_index = 0

        if not current_day:
            continue

        if len(cells) <= lesson_cell_index:
            continue

        lesson_text = clean_text(cells[lesson_cell_index].get_text(" ", strip=True))
        lesson = parse_lesson_header(lesson_text)

        if not lesson:
            continue

        info_cells = cells[lesson_cell_index + 1:]
        lesson_items = []

        for index, info_cell in enumerate(info_cells):
            classes = info_cell.get("class", [])

            if "ur" not in classes:
                continue

            item = parse_lesson_info(info_cell)

            if not item:
                continue

            colspan = info_cell.get("colspan")
            subgroup = None

            if colspan != "2" and len(info_cells) > 1:
                subgroup = index + 1

            item["subgroup"] = subgroup
            lesson_items.append(item)

        if lesson_items:
            lesson["items"] = lesson_items
            current_day["lessons"].append(lesson)

    updated = ""
    ref = soup.find("div", class_="ref")

    if ref:
        updated = clean_text(ref.get_text(" ", strip=True))

    schedule.sort(
        key=lambda day: parse_date(day["date"]) or datetime.max.date()
    )

    return {
        "group": group_name,
        "url": group_url,
        "updated": updated,
        "days": schedule
    }


# ==========================
# ДЕНЬ: ПОДРОБНЫЙ ВЫВОД
# ==========================

def format_lesson_item_detailed(item):
    subject = shorten_subject(item.get("subject", ""))
    subject = escape_html_text(subject)

    room = escape_html_text(item.get("room", ""))
    teacher = escape_html_text(item.get("teacher", ""))
    subgroup = item.get("subgroup")

    prefix = important_prefix(subject)

    lines = []

    subject_line = f"{prefix}<b>{subject}</b>"

    if subgroup:
        subject_line += f" · п/г {subgroup}"

    lines.append(subject_line)

    if room:
        lines.append(f"🏫 Кабинет: <b>{room}</b>")

    if teacher:
        lines.append(f"👤 Преподаватель: {teacher}")

    return lines


def format_day_detailed(schedule_data, day, title_prefix: str) -> str:
    day_label = get_day_label(day)

    total_time = get_day_time_range(day)
    lessons_count = count_day_lessons(day)

    lines = [
        f"{title_prefix}",
        get_updated_line(schedule_data),
        "",
        f"📍 <b>{day['date']}</b> · {day_label}",
    ]

    if total_time:
        lines.append(f"⏱ Общее время: <b>{total_time}</b>")

    lines.append(f"🔢 Пар: <b>{lessons_count}</b>")

    if not day.get("lessons"):
        lines.append("")
        lines.append("Пар нет 🎉")
        return "\n".join(lines)

    for lesson in day["lessons"]:
        time_text = normalize_time(lesson.get("time", ""))

        lines.append("")
        lines.append("━━━━━━━━━━━━━━")
        lines.append(f"🔹 <b>{lesson['number']} пара</b> · {time_text}")

        for index, item in enumerate(lesson["items"]):
            if index > 0:
                lines.append("")

            lines.extend(format_lesson_item_detailed(item))

    return "\n".join(lines)


def find_day_by_date(schedule_data, date_str: str):
    for day in schedule_data["days"]:
        if day["date"] == date_str:
            return day

    return None


def get_day_by_offset(schedule_data, offset: int):
    target_date = now_local_date() + timedelta(days=offset)
    target_str = target_date.strftime("%d.%m.%Y")
    return find_day_by_date(schedule_data, target_str), target_str


# ==========================
# НЕДЕЛЯ: ОБЗОР + КНОПКИ ДНЕЙ
# ==========================

def get_relevant_week_days(schedule_data):
    today = now_local_date()
    week_end = today + timedelta(days=6)

    result = []

    for day in schedule_data["days"]:
        day_date = parse_date(day["date"])

        if not day_date:
            continue

        if today <= day_date <= week_end:
            result.append(day)

    if result:
        return result

    return schedule_data["days"][:7]


def get_day_flags(day):
    has_credit = False
    has_exam = False
    has_consultation = False

    for lesson in day.get("lessons", []):
        for item in lesson.get("items", []):
            subject = item.get("subject", "").lower()

            if "экзамен" in subject:
                has_exam = True

            if "зач" in subject:
                has_credit = True

            if "консультац" in subject:
                has_consultation = True

    flags = []

    if has_exam:
        flags.append("🚨 экзамен")

    if has_credit:
        flags.append("⚠️ зачёт")

    if has_consultation:
        flags.append("💬 консультация")

    return flags


def format_week_day_line(day) -> str:
    date_short = pretty_short_date(day["date"])
    day_label = get_day_label(day)

    lessons_count = count_day_lessons(day)

    if lessons_count == 0:
        return f"<b>{day_label} · {date_short}</b> — пар нет"

    total_time = get_day_time_range(day)
    flags = get_day_flags(day)

    parts = []

    if total_time:
        parts.append(total_time)

    parts.append(f"{lessons_count} {plural_lessons(lessons_count)}")

    if flags:
        parts.append(", ".join(flags))

    return f"<b>{day_label} · {date_short}</b> — " + " · ".join(parts)


def format_week_overview(schedule_data):
    days = get_relevant_week_days(schedule_data)

    lines = [
        f"📅 <b>Неделя · {escape_html_text(schedule_data['group'])}</b>",
        get_updated_line(schedule_data),
        "",
    ]

    if not days:
        lines.append("Расписание не найдено.")
        return "\n".join(lines), []

    first_day = pretty_short_date(days[0]["date"])
    last_day = pretty_short_date(days[-1]["date"])
    total_lessons = sum(count_day_lessons(day) for day in days)

    lines.append(f"Период: <b>{first_day} — {last_day}</b>")
    lines.append(f"Всего: <b>{total_lessons} {plural_lessons(total_lessons)}</b>")
    lines.append("")
    lines.append("Выбери день ниже:")

    lines.append("")

    for day in days:
        lines.append(format_week_day_line(day))

    return "\n".join(lines), days


# ==========================
# ОБРАБОТЧИКИ ЭКРАНОВ
# ==========================

async def show_start(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    text = (
        "Бот расписания ВГЭК ✅\n\n"
        "Выбери действие:\n\n"
        "📌 Сегодня — подробное расписание\n"
        "➡️ Завтра — подробное расписание\n"
        "📅 Неделя — обзор и кнопки дней\n\n"
        f"Группа: <b>{DEFAULT_GROUP}</b>"
    )

    if edit:
        await edit_screen(update, context, text, reply_markup=main_keyboard())
    else:
        await send_new_screen(update, context, text, reply_markup=main_keyboard())


async def show_today(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = True):
    try:
        schedule = parse_group_schedule(DEFAULT_GROUP)
        day, target_str = get_day_by_offset(schedule, 0)

        context.user_data["last_view"] = "today"

        if day:
            text = format_day_detailed(schedule, day, "📌 <b>Сегодня</b>")
        else:
            text = (
                "📌 <b>Сегодня</b>\n"
                f"{get_updated_line(schedule)}\n\n"
                f"Не нашёл расписание на {target_str}."
            )

    except Exception as error:
        text = f"❌ Ошибка:\n{escape_html_text(error)}"

    if edit:
        await edit_screen(update, context, text, reply_markup=day_keyboard())
    else:
        await send_new_screen(update, context, text, reply_markup=day_keyboard())


async def show_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = True):
    try:
        schedule = parse_group_schedule(DEFAULT_GROUP)
        day, target_str = get_day_by_offset(schedule, 1)

        context.user_data["last_view"] = "tomorrow"

        if day:
            text = format_day_detailed(schedule, day, "➡️ <b>Завтра</b>")
        else:
            text = (
                "➡️ <b>Завтра</b>\n"
                f"{get_updated_line(schedule)}\n\n"
                f"Не нашёл расписание на {target_str}."
            )

    except Exception as error:
        text = f"❌ Ошибка:\n{escape_html_text(error)}"

    if edit:
        await edit_screen(update, context, text, reply_markup=day_keyboard())
    else:
        await send_new_screen(update, context, text, reply_markup=day_keyboard())


async def show_week(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = True):
    try:
        schedule = parse_group_schedule(DEFAULT_GROUP)
        text, days = format_week_overview(schedule)

        context.user_data["last_view"] = "week"

        markup = week_keyboard(days)

    except Exception as error:
        text = f"❌ Ошибка:\n{escape_html_text(error)}"
        markup = main_keyboard()

    if edit:
        await edit_screen(update, context, text, reply_markup=markup)
    else:
        await send_new_screen(update, context, text, reply_markup=markup)


async def show_specific_day(update: Update, context: ContextTypes.DEFAULT_TYPE, date_str: str):
    try:
        schedule = parse_group_schedule(DEFAULT_GROUP)
        day = find_day_by_date(schedule, date_str)

        context.user_data["last_view"] = "day"
        context.user_data["last_date"] = date_str

        if day:
            day_label = get_day_label(day)
            date_short = pretty_short_date(day["date"])
            title = f"📅 <b>{day_label} · {date_short}</b>"
            text = format_day_detailed(schedule, day, title)
        else:
            text = (
                f"📅 <b>{escape_html_text(date_str)}</b>\n"
                f"{get_updated_line(schedule)}\n\n"
                "Расписание на этот день не найдено."
            )

    except Exception as error:
        text = f"❌ Ошибка:\n{escape_html_text(error)}"

    await edit_screen(update, context, text, reply_markup=day_keyboard())


async def refresh_current(update: Update, context: ContextTypes.DEFAULT_TYPE):
    last_view = context.user_data.get("last_view", "today")

    if last_view == "today":
        await show_today(update, context, edit=True)
        return

    if last_view == "tomorrow":
        await show_tomorrow(update, context, edit=True)
        return

    if last_view == "week":
        await show_week(update, context, edit=True)
        return

    if last_view == "day":
        date_str = context.user_data.get("last_date")
        if date_str:
            await show_specific_day(update, context, date_str)
            return

    await show_today(update, context, edit=True)


# ==========================
# TELEGRAM HANDLERS
# ==========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_start(update, context, edit=False)


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_today(update, context, edit=False)


async def tomorrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_tomorrow(update, context, edit=False)


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_week(update, context, edit=False)


async def group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_new_screen(
        update,
        context,
        "Сейчас бот настроен на группу "
        f"<b>{DEFAULT_GROUP}</b>.\n\n"
        "Выбор другой группы добавим позже.",
        reply_markup=main_keyboard(),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "today":
        await show_today(update, context, edit=True)
        return

    if data == "tomorrow":
        await show_tomorrow(update, context, edit=True)
        return

    if data == "week":
        await show_week(update, context, edit=True)
        return

    if data == "refresh":
        await refresh_current(update, context)
        return

    if data.startswith("day:"):
        date_str = data.replace("day:", "", 1)
        await show_specific_day(update, context, date_str)
        return

    await show_start(update, context, edit=True)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_new_screen(
        update,
        context,
        "Используй кнопки под сообщением 👇",
        reply_markup=main_keyboard(),
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("ОШИБКА:")
    print(context.error)


# ==========================
# ЗАПУСК
# ==========================

def main():
    if not BOT_TOKEN:
        print("ОШИБКА: BOT_TOKEN не найден в переменных окружения")
        return

    print("Токен найден.")
    print("Запускаю бота...")

    builder = Application.builder().token(BOT_TOKEN)

    if PROXY_URL:
        builder = (
            builder
            .proxy(PROXY_URL)
            .get_updates_proxy(PROXY_URL)
        )

    app = (
        builder
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("tomorrow", tomorrow_command))
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("group", group_command))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_error_handler(error_handler)

    print("Бот запущен.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
