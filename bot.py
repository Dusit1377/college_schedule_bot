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

# Если PROXY_URL пустой — бот работает без прокси.
# На хостинге обычно прокси не нужен.
PROXY_URL = os.getenv("PROXY_URL", "").strip()

REQUEST_PROXIES = None
if PROXY_URL:
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

LAST_MESSAGES_BY_CHAT = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


# ==========================
# ОЧИСТКА ЧАТА
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


async def send_tracked_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    parse_mode: str | None = None,
    reply_markup=None
):
    chat_id = update.effective_chat.id

    message = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup
    )

    LAST_MESSAGES_BY_CHAT.setdefault(chat_id, []).append(message.message_id)

    return message


async def prepare_clean_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_old_bot_messages(update, context)
    await delete_user_message(update, context)


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

            updated_line = get_updated_line(schedule_data)
            day_text = format_day_detailed(day, show_empty=True)

            return limit_message(f"{prefix}\n{updated_line}\n\n{day_text}")

    updated_line = get_updated_line(schedule_data)
    return f"{updated_line}\n\nНе нашёл расписание на {target_str}."


# ==========================
# КОМПАКТНАЯ НЕДЕЛЯ
# ==========================

def get_relevant_week_days(schedule_data):
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


def get_main_subjects_for_day(day) -> list[str]:
    subjects = []

    for lesson in day.get("lessons", []):
        for item in lesson.get("items", []):
            subject = item.get("subject", "")

            if not subject:
                continue

            subjects.append(subject)

    return subjects


def shorten_subject(subject: str) -> str:
    subject = subject.replace("Консультация к экзамену по дисциплине", "Конс.")
    subject = subject.replace("Диф.зач.", "Диф.зач.")
    subject = subject.replace("Диф. зач.", "Диф.зач.")
    subject = subject.replace("Основы ораторского искусства", "Ораторское")
    subject = subject.replace("История мировой и отечественной культуры", "ИМК")
    return subject.strip()


def compact_subjects_line(subjects: list[str]) -> str:
    counts = {}

    for subject in subjects:
        subject = shorten_subject(subject)

        if subject not in counts:
            counts[subject] = 0

        counts[subject] += 1

    parts = []

    for subject, count in counts.items():
        if count > 1:
            parts.append(f"{subject} ×{count}")
        else:
            parts.append(subject)

    return ", ".join(parts)


def format_week_day_compact(day) -> str:
    date_short = pretty_short_date(day["date"])
    weekday = day["weekday"]

    week_type = ""
    if day.get("week_type"):
        week_type = f"-{day['week_type']}"

    total_time = get_day_time_range(day)
    lessons_count = count_day_lessons(day)
    subjects = get_main_subjects_for_day(day)
    subjects_line = compact_subjects_line(subjects)

    lines = [
        f"<b>{weekday}{week_type} · {date_short}</b>"
    ]

    meta = []

    if total_time:
        meta.append(f"⏱ {total_time}")

    meta.append(f"🔢 {lessons_count} пар.")

    lines.append(" · ".join(meta))

    if subjects_line:
        lines.append(f"📚 {escape_html_text(subjects_line)}")

    return "\n".join(lines)


def format_week(schedule_data):
    days = get_relevant_week_days(schedule_data)

    lines = [
        f"📅 <b>Ближайшая неделя · {escape_html_text(schedule_data['group'])}</b>",
        get_updated_line(schedule_data),
    ]

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
    await prepare_clean_response(update, context)

    await send_tracked_message(
        update,
        context,
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
    await prepare_clean_response(update, context)

    await send_tracked_message(
        update,
        context,
        "pong ✅",
        reply_markup=MAIN_KEYBOARD
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await prepare_clean_response(update, context)

    loading = await send_tracked_message(
        update,
        context,
        "Загружаю сегодня…",
        reply_markup=MAIN_KEYBOARD
    )

    try:
        schedule = parse_group_schedule(DEFAULT_GROUP)
        result = format_day_by_offset(schedule, 0)
    except Exception as error:
        result = f"❌ Ошибка:\n{escape_html_text(error)}"

    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=loading.message_id
        )
        LAST_MESSAGES_BY_CHAT[update.effective_chat.id].remove(loading.message_id)
    except Exception:
        pass

    await send_tracked_message(
        update,
        context,
        result,
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD
    )


async def tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await prepare_clean_response(update, context)

    loading = await send_tracked_message(
        update,
        context,
        "Загружаю завтра…",
        reply_markup=MAIN_KEYBOARD
    )

    try:
        schedule = parse_group_schedule(DEFAULT_GROUP)
        result = format_day_by_offset(schedule, 1)
    except Exception as error:
        result = f"❌ Ошибка:\n{escape_html_text(error)}"

    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=loading.message_id
        )
        LAST_MESSAGES_BY_CHAT[update.effective_chat.id].remove(loading.message_id)
    except Exception:
        pass

    await send_tracked_message(
        update,
        context,
        result,
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD
    )


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await prepare_clean_response(update, context)

    loading = await send_tracked_message(
        update,
        context,
        "Собираю краткий обзор недели…",
        reply_markup=MAIN_KEYBOARD
    )

    try:
        schedule = parse_group_schedule(DEFAULT_GROUP)
        messages = format_week(schedule)
    except Exception as error:
        messages = [f"❌ Ошибка:\n{escape_html_text(error)}"]

    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=loading.message_id
        )
        LAST_MESSAGES_BY_CHAT[update.effective_chat.id].remove(loading.message_id)
    except Exception:
        pass

    for message in messages:
        await send_tracked_message(
            update,
            context,
            message,
            parse_mode="HTML",
            reply_markup=MAIN_KEYBOARD
        )


async def group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await prepare_clean_response(update, context)

    if not context.args:
        await send_tracked_message(
            update,
            context,
            "Напиши группу после команды. Например:\n/group Д-12",
            reply_markup=MAIN_KEYBOARD
        )
        return

    group_name = " ".join(context.args).strip()

    loading = await send_tracked_message(
        update,
        context,
        f"Ищу расписание группы {group_name}…",
        reply_markup=MAIN_KEYBOARD
    )

    try:
        schedule = parse_group_schedule(group_name)
        messages = format_week(schedule)
    except Exception as error:
        messages = [f"❌ Ошибка:\n{escape_html_text(error)}"]

    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=loading.message_id
        )
        LAST_MESSAGES_BY_CHAT[update.effective_chat.id].remove(loading.message_id)
    except Exception:
        pass

    for message in messages:
        await send_tracked_message(
            update,
            context,
            message,
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

    await prepare_clean_response(update, context)

    await send_tracked_message(
        update,
        context,
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
