import os
import re
import html
import logging
import requests
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

BASE_URL = "https://vgek43.ru"
DEFAULT_GROUP = "Д-12"

# Твой рабочий HTTP-прокси
PROXY_URL = "http://hW5fiTZE:Yiq5KNNt@130.49.81.142:63446"

REQUEST_PROXIES = {
    "http": PROXY_URL,
    "https": PROXY_URL,
}

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📌 Сегодня", "➡️ Завтра"],
        ["📅 Неделя", "🔄 Обновить"],
    ],
    resize_keyboard=True
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
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


def pretty_short_date(date_text: str) -> str:
    parts = date_text.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return date_text


def get_subject_icon(subject: str) -> str:
    subject_lower = subject.lower()

    if "зач" in subject_lower:
        return "⚠️"
    if "экзамен" in subject_lower:
        return "🚨"
    if "консультация" in subject_lower:
        return "💬"
    if "матем" in subject_lower:
        return "🧮"
    if "русский" in subject_lower:
        return "📝"
    if "литера" in subject_lower:
        return "📖"
    if "информ" in subject_lower:
        return "💻"
    if "иностран" in subject_lower or "англий" in subject_lower:
        return "🇬🇧"
    if "рисунок" in subject_lower:
        return "🎨"
    if "история" in subject_lower:
        return "🏛"
    if "общество" in subject_lower:
        return "👥"
    if "проект" in subject_lower:
        return "📌"

    return "📘"


def parse_time_range(time_text: str):
    """
    14.20-15.40 или 14:20–15:40 -> ('14:20', '15:40')
    """
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
    """
    Возвращает общее время дня: 14:20–20:00
    """
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
# ПОДРОБНЫЙ ВЫВОД ДНЯ
# ==========================

def format_lesson_item_detailed(item):
    subject = escape_html_text(item.get("subject", ""))
    room = escape_html_text(item.get("room", ""))
    teacher = escape_html_text(item.get("teacher", ""))
    subgroup = item.get("subgroup")

    icon = get_subject_icon(subject)

    lines = []

    if subgroup:
        lines.append(f"{icon} <b>Подгруппа {subgroup}: {subject}</b>")
    else:
        lines.append(f"{icon} <b>{subject}</b>")

    if room:
        lines.append(f"🏫 Кабинет: <b>{room}</b>")

    if teacher:
        lines.append(f"👤 Преподаватель: {teacher}")

    return lines


def format_day_detailed(day, show_empty: bool = False) -> str:
    date_full = day["date"]
    weekday = day["weekday"]

    week_type = ""
    if day.get("week_type"):
        week_type = f"-{day['week_type']}"

    title = f"📍 <b>{date_full}</b> · {weekday}{week_type}"

    if not day["lessons"]:
        if show_empty:
            return f"{title}\n\nПар нет 🎉"
        return ""

    total_time = get_day_time_range(day)
    lessons_count = count_day_lessons(day)

    lines = [title]

    if total_time:
        lines.append(f"⏱ Общее время: <b>{total_time}</b>")

    lines.append(f"🔢 Пар: <b>{lessons_count}</b>")

    for lesson in day["lessons"]:
        time_text = normalize_time(lesson.get("time", ""))

        lines.append("")
        lines.append("━━━━━━━━━━━━━━")
        lines.append(f"🔹 <b>{lesson['number']} пара</b>")
        lines.append(f"⏰ {time_text}")

        for index, item in enumerate(lesson["items"]):
            if index > 0:
                lines.append("")

            lines.extend(format_lesson_item_detailed(item))

    return "\n".join(lines)


def format_day_by_offset(schedule_data, offset: int) -> str:
    target_date = datetime.now().date() + timedelta(days=offset)
    target_str = target_date.strftime("%d.%m.%Y")

    for day in schedule_data["days"]:
        if day["date"] == target_str:
            if offset == 0:
                prefix = "📌 <b>Сегодня</b>"
            elif offset == 1:
                prefix = "➡️ <b>Завтра</b>"
            else:
                prefix = f"📌 <b>{target_str}</b>"

            day_text = format_day_detailed(day, show_empty=True)

            return limit_message(f"{prefix}\n\n{day_text}")

    return f"Не нашёл расписание на {target_str}."


# ==========================
# КОМПАКТНАЯ НЕДЕЛЯ
# ==========================

def get_relevant_week_days(schedule_data):
    """
    Берём только ближайшие 7 дней от сегодняшней даты.
    Если сайт показывает не этот диапазон, берём первые 7 дней, где есть пары.
    """
    today = datetime.now().date()
    week_end = today + timedelta(days=6)

    result = []

    for day in schedule_data["days"]:
        day_date = parse_date(day["date"])

        if not day_date:
            continue

        if today <= day_date <= week_end and day.get("lessons"):
            result.append(day)

    if result:
        return result

    fallback = []

    for day in schedule_data["days"]:
        if day.get("lessons"):
            fallback.append(day)

        if len(fallback) >= 7:
            break

    return fallback


def get_compact_subjects_for_day(day) -> list[str]:
    subjects = []

    for lesson in day.get("lessons", []):
        lesson_number = lesson.get("number", "")
        time_text = normalize_time(lesson.get("time", ""))

        item_names = []

        for item in lesson.get("items", []):
            subject = item.get("subject", "")
            room = item.get("room", "")
            subgroup = item.get("subgroup")

            if subgroup:
                name = f"п/г {subgroup}: {subject}"
            else:
                name = subject

            if room:
                name += f" · {room}"

            item_names.append(name)

        if not item_names:
            continue

        joined_items = " / ".join(item_names)
        subjects.append(f"{lesson_number}) {time_text} — {joined_items}")

    return subjects


def format_week_day_compact(day) -> str:
    date_short = pretty_short_date(day["date"])
    weekday = day["weekday"]

    week_type = ""
    if day.get("week_type"):
        week_type = f"-{day['week_type']}"

    total_time = get_day_time_range(day)
    lessons_count = count_day_lessons(day)

    header = f"<b>{weekday}{week_type} · {date_short}</b>"

    meta = []

    if total_time:
        meta.append(f"⏱ {total_time}")

    meta.append(f"🔢 {lessons_count} пар.")

    lines = [
        header,
        " · ".join(meta)
    ]

    subjects = get_compact_subjects_for_day(day)

    # Чтобы неделя не была спамом:
    # показываем максимум 4 строки пар, если пар больше — сворачиваем хвост.
    max_lines = 4

    for subject_line in subjects[:max_lines]:
        lines.append(escape_html_text(subject_line))

    if len(subjects) > max_lines:
        lines.append(f"…ещё {len(subjects) - max_lines} пар.")

    return "\n".join(lines)


def format_week(schedule_data):
    days = get_relevant_week_days(schedule_data)

    lines = [
        f"📅 <b>Ближайшая неделя · {escape_html_text(schedule_data['group'])}</b>",
    ]

    if schedule_data["updated"]:
        lines.append(f"🕒 {escape_html_text(schedule_data['updated'])}")

    lines.append("")

    if not days:
        lines.append("На ближайшую неделю занятий не найдено.")
        return ["\n".join(lines)]

    total_lessons = sum(count_day_lessons(day) for day in days)
    first_day = days[0]["date"]
    last_day = days[-1]["date"]

    lines.append(f"Период: <b>{first_day} — {last_day}</b>")
    lines.append(f"Всего пар: <b>{total_lessons}</b>")
    lines.append("")

    for day in days:
        lines.append(format_week_day_compact(day))
        lines.append("")

    text = "\n".join(lines).strip()

    return split_long_message(text)


# ==========================
# ДЕЛЕНИЕ СООБЩЕНИЙ
# ==========================

def limit_message(text: str) -> str:
    if len(text) <= 3900:
        return text

    return text[:3900] + "\n\n…обрезал, потому что сообщение слишком длинное."


def split_long_message(text: str, limit: int = 3600):
    if len(text) <= limit:
        return [text]

    parts = []
    current = ""

    blocks = text.split("\n\n")

    for block in blocks:
        if len(current) + len(block) + 2 > limit:
            if current.strip():
                parts.append(current.strip())
            current = block
        else:
            if current:
                current += "\n\n" + block
            else:
                current = block

    if current.strip():
        parts.append(current.strip())

    return parts


# ==========================
# TELEGRAM-КОМАНДЫ
# ==========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Бот живой ✅\n\n"
        "Выбери кнопку ниже:\n\n"
        "📌 Сегодня — подробное расписание\n"
        "➡️ Завтра — подробное расписание\n"
        "📅 Неделя — краткий обзор ближайших 7 дней\n"
        "🔄 Обновить — обновить сегодня\n\n"
        f"Группа по умолчанию: {DEFAULT_GROUP}",
        reply_markup=MAIN_KEYBOARD
    )


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "pong ✅",
        reply_markup=MAIN_KEYBOARD
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Загружаю сегодня…",
        reply_markup=MAIN_KEYBOARD
    )

    try:
        schedule = parse_group_schedule(DEFAULT_GROUP)
        result = format_day_by_offset(schedule, 0)
    except Exception as error:
        result = f"❌ Ошибка:\n{escape_html_text(error)}"

    await update.message.reply_text(
        result,
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD
    )


async def tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Загружаю завтра…",
        reply_markup=MAIN_KEYBOARD
    )

    try:
        schedule = parse_group_schedule(DEFAULT_GROUP)
        result = format_day_by_offset(schedule, 1)
    except Exception as error:
        result = f"❌ Ошибка:\n{escape_html_text(error)}"

    await update.message.reply_text(
        result,
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD
    )


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Собираю краткий обзор недели…",
        reply_markup=MAIN_KEYBOARD
    )

    try:
        schedule = parse_group_schedule(DEFAULT_GROUP)
        messages = format_week(schedule)

        for message in messages:
            await update.message.reply_text(
                message,
                parse_mode="HTML",
                reply_markup=MAIN_KEYBOARD
            )

    except Exception as error:
        await update.message.reply_text(
            f"❌ Ошибка:\n{escape_html_text(error)}",
            parse_mode="HTML",
            reply_markup=MAIN_KEYBOARD
        )


async def group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Напиши группу после команды. Например:\n/group Д-12",
            reply_markup=MAIN_KEYBOARD
        )
        return

    group_name = " ".join(context.args).strip()

    await update.message.reply_text(
        f"Ищу расписание группы {group_name}…",
        reply_markup=MAIN_KEYBOARD
    )

    try:
        schedule = parse_group_schedule(group_name)
        messages = format_week(schedule)

        for message in messages:
            await update.message.reply_text(
                message,
                parse_mode="HTML",
                reply_markup=MAIN_KEYBOARD
            )

    except Exception as error:
        await update.message.reply_text(
            f"❌ Ошибка:\n{escape_html_text(error)}",
            parse_mode="HTML",
            reply_markup=MAIN_KEYBOARD
        )


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "📌 Сегодня":
        await today(update, context)
        return

    if text == "➡️ Завтра":
        await tomorrow(update, context)
        return

    if text == "📅 Неделя":
        await week(update, context)
        return

    if text == "🔄 Обновить":
        await today(update, context)
        return

    await update.message.reply_text(
        "Не понял сообщение. Используй кнопки ниже 👇",
        reply_markup=MAIN_KEYBOARD
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("ОШИБКА:")
    print(context.error)


# ==========================
# ЗАПУСК
# ==========================

def main():
    if not BOT_TOKEN:
        print("ОШИБКА: BOT_TOKEN не найден в .env")
        return

    print("Токен найден.")
    print("Запускаю бота...")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .proxy(PROXY_URL)
        .get_updates_proxy(PROXY_URL)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("tomorrow", tomorrow))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("group", group))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button))

    app.add_error_handler(error_handler)

    print("Бот запущен. Напиши /start в Telegram")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()