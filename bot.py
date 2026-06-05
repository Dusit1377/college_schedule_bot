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
        ["📅 Неделя", "🗓 По дате"],
        ["🔄 Обновить"],
    ],
    resize_keyboard=True
)

LAST_MESSAGES_BY_CHAT = {}
WAITING_DATE_BY_CHAT = set()

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


async def remove_loading_message(update: Update, context: ContextTypes.DEFAULT_TYPE, loading_message):
    chat_id = update.effective_chat.id

    try:
        await context.bot.delete_message(
            chat_id=chat_id,
            message_id=loading_message.message_id
        )

        if loading_message.message_id in LAST_MESSAGES_BY_CHAT.get(chat_id, []):
            LAST_MESSAGES_BY_CHAT[chat_id].remove(loading_message.message_id)

    except Exception:
        pass


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


def now_local_datetime():
    # Киров / Москва: UTC+3
    return datetime.utcnow() + timedelta(hours=3)


def now_local_date():
    return now_local_datetime().date()


def parse_date(date_text: str):
    try:
        return datetime.strptime(date_text, "%d.%m.%Y").date()
    except Exception:
        return None


def parse_user_short_date(text: str):
    """
    05.06 -> 05.06.2026
    """
    text = text.strip()

    if not re.fullmatch(r"\d{2}\.\d{2}", text):
        return None

    year = now_local_date().year
    return f"{text}.{year}"


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


def get_day_label(day) -> str:
    week_type = ""

    if day.get("week_type"):
        week_type = f"-{day['week_type']}"

    return f"{day['weekday']}{week_type}"


def get_updated_line(schedule_data) -> str:
    updated = schedule_data.get("updated", "")

    if not updated:
        return "🕒 Время обновления не найдено"

    return f"🕒 {escape_html_text(updated)}"


def normalize_subject_for_icon(subject: str) -> str:
    return subject.lower().replace("ё", "е")


def get_subject_icon(subject: str) -> str:
    subject_lower = normalize_subject_for_icon(subject)

    if "экзамен" in subject_lower:
        return "🚨"
    if "зач" in subject_lower:
        return "⚠️"
    if "консультац" in subject_lower:
        return "💬"
    if "рисунок" in subject_lower:
        return "🎨"
    if "русский" in subject_lower:
        return "📝"
    if "литера" in subject_lower:
        return "📖"
    if "матем" in subject_lower:
        return "🧮"
    if "информ" in subject_lower:
        return "💻"
    if "иностран" in subject_lower or "англий" in subject_lower:
        return "🇬🇧"
    if "история мировой" in subject_lower or "имк" in subject_lower:
        return "🏛"
    if "история" in subject_lower:
        return "🏛"
    if "общество" in subject_lower:
        return "👥"
    if "проект" in subject_lower:
        return "📌"

    return "📘"


def shorten_subject(subject: str) -> str:
    subject = subject.strip()

    replacements = {
        "Консультация к экзамену по дисциплине История мировой и отечественной культуры": "Конс. ИМК",
        "Консультация к экзамену по дисциплине Математика": "Конс. Математика",
        "Консультация к экзамену по дисциплине Русский язык": "Конс. Русский",
        "История мировой и отечественной культуры": "ИМК",
        "Основы ораторского искусства": "Ораторское",
        "Диф. зач.": "Диф.зач.",
        "Диф.зач.": "Диф.зач.",
    }

    for old, new in replacements.items():
        subject = subject.replace(old, new)

    return subject.strip()


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


def time_to_minutes(time_text: str):
    """
    17:10 -> 1030 минут
    """
    if not time_text:
        return None

    match = re.search(r"(\d{1,2}):(\d{2})", time_text)

    if not match:
        return None

    hours = int(match.group(1))
    minutes = int(match.group(2))

    return hours * 60 + minutes


def get_last_lesson_end_minutes(day):
    """
    Возвращает время окончания последней пары в минутах.
    """
    last_end = None

    for lesson in day.get("lessons", []):
        parsed = parse_time_range(lesson.get("time", ""))

        if not parsed:
            continue

        start, end = parsed
        end_minutes = time_to_minutes(end)

        if end_minutes is None:
            continue

        if last_end is None or end_minutes > last_end:
            last_end = end_minutes

    return last_end


def is_today_finished(day) -> bool:
    """
    True, если сегодня все пары уже закончились.
    """
    day_date = parse_date(day["date"])

    if day_date != now_local_date():
        return False

    last_end = get_last_lesson_end_minutes(day)

    if last_end is None:
        return False

    current = now_local_datetime()
    current_minutes = current.hour * 60 + current.minute

    return current_minutes >= last_end


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
# ПОДРОБНЫЙ ДЕНЬ
# ==========================

def format_lesson_item_detailed(item):
    original_subject = item.get("subject", "")
    subject = shorten_subject(original_subject)

    icon = get_subject_icon(original_subject)

    subject_safe = escape_html_text(subject)
    room = escape_html_text(item.get("room", ""))
    teacher = escape_html_text(item.get("teacher", ""))
    subgroup = item.get("subgroup")

    subject_line = f"{icon} <b>{subject_safe}</b>"

    if subgroup:
        subject_line += f" · п/г {subgroup}"

    lines = [subject_line]

    location_parts = []

    if room:
        location_parts.append(f"🏫 {room}")

    if teacher:
        location_parts.append(f"👤 {teacher}")

    if location_parts:
        lines.append(" · ".join(location_parts))

    return lines


def format_day_detailed(schedule_data, day, title: str) -> str:
    day_label = get_day_label(day)
    total_time = get_day_time_range(day)
    lessons_count = count_day_lessons(day)

    lines = [
        title,
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
        lines.append(f"🔹 <b>{lesson['number']} пара</b>")
        lines.append(f"⏰ {time_text}")

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


def format_day_by_offset(schedule_data, offset: int) -> str:
    day, target_str = get_day_by_offset(schedule_data, offset)

    if offset == 0:
        title = "📌 <b>Сегодня</b>"
    elif offset == 1:
        title = "➡️ <b>Завтра</b>"
    else:
        title = f"📌 <b>{target_str}</b>"

    if day:
        return limit_message(format_day_detailed(schedule_data, day, title))

    return (
        f"{title}\n"
        f"{get_updated_line(schedule_data)}\n\n"
        f"Не нашёл расписание на {target_str}."
    )


# ==========================
# СВОДКА ПО ДАТЕ
# ==========================

def get_lesson_main_subject(lesson) -> str:
    names = []

    for item in lesson.get("items", []):
        subject = shorten_subject(item.get("subject", ""))

        if not subject:
            continue

        subgroup = item.get("subgroup")

        if subgroup:
            subject += f" · п/г {subgroup}"

        names.append(subject)

    return " / ".join(names)


def get_other_subjects_after_first(day) -> list[str]:
    subjects = []

    for lesson in day.get("lessons", [])[1:]:
        for item in lesson.get("items", []):
            subject = item.get("subject", "")
            if subject:
                subjects.append(subject)

    return subjects


def group_subjects_for_summary(subjects: list[str]) -> list[str]:
    grouped = {}

    for subject in subjects:
        short = shorten_subject(subject)
        icon = get_subject_icon(subject)
        key = f"{icon} {short}"

        if key not in grouped:
            grouped[key] = 0

        grouped[key] += 1

    result = []

    for name, count in grouped.items():
        if count > 1:
            result.append(f"{name} ×{count}")
        else:
            result.append(name)

    return result


def format_date_summary(schedule_data, day) -> str:
    day_label = get_day_label(day)
    date_short = pretty_short_date(day["date"])
    total_time = get_day_time_range(day)
    lessons_count = count_day_lessons(day)

    lines = [
        f"🗓 <b>Сводка на {date_short}</b> · {day_label}",
        get_updated_line(schedule_data),
        "",
    ]

    if not day.get("lessons"):
        lines.append("Пар нет 🎉")
        return "\n".join(lines)

    first_lesson = day["lessons"][0]
    first_time = normalize_time(first_lesson.get("time", ""))
    first_range = parse_time_range(first_time)

    first_start = first_range[0] if first_range else first_time
    first_subject_raw = get_lesson_main_subject(first_lesson)
    first_subject_icon = get_subject_icon(first_subject_raw)
    first_subject = escape_html_text(first_subject_raw)

    lines.append(f"К <b>{first_start}</b>")
    lines.append(f"Первая пара — {first_subject_icon} <b>{first_subject}</b>")

    other_subjects = get_other_subjects_after_first(day)
    grouped_others = group_subjects_for_summary(other_subjects)

    if grouped_others:
        lines.append("")
        lines.append("<b>Остальные:</b>")
        for subject in grouped_others:
            lines.append(escape_html_text(subject))

    lines.append("")

    if total_time:
        lines.append(f"⏱ <b>{total_time}</b>")

    lines.append(f"🔢 <b>{lessons_count} {plural_lessons(lessons_count)}</b>")

    return "\n".join(lines)


# ==========================
# НЕДЕЛЯ
# ==========================

def get_relevant_week_days(schedule_data):
    today = now_local_date()
    week_end = today + timedelta(days=6)

    result = []

    for day in schedule_data["days"]:
        day_date = parse_date(day["date"])

        if not day_date:
            continue

        if not day.get("lessons"):
            continue

        if not (today <= day_date <= week_end):
            continue

        # Если сегодняшний учебный день уже закончился — в неделе его не показываем
        if day_date == today and is_today_finished(day):
            continue

        result.append(day)

    if result:
        return result

    fallback = []

    for day in schedule_data["days"]:
        day_date = parse_date(day["date"])

        if not day.get("lessons"):
            continue

        if day_date == today and is_today_finished(day):
            continue

        fallback.append(day)

        if len(fallback) >= 7:
            break

    return fallback


def get_subjects_for_day(day):
    subjects = []

    for lesson in day.get("lessons", []):
        for item in lesson.get("items", []):
            subject = item.get("subject", "")

            if subject:
                subjects.append(subject)

    return subjects


def group_subjects(subjects):
    grouped = {}

    for subject in subjects:
        short = shorten_subject(subject)
        icon = get_subject_icon(subject)
        key = f"{icon} {short}"

        if key not in grouped:
            grouped[key] = 0

        grouped[key] += 1

    return grouped


def format_week_day_card(day) -> str:
    day_label = get_day_label(day)
    date_short = pretty_short_date(day["date"])
    total_time = get_day_time_range(day)
    lessons_count = count_day_lessons(day)

    lines = [
        "━━━━━━━━━━━━━━",
        f"📍 <b>{day_label} · {date_short}</b>",
    ]

    meta = []

    if total_time:
        meta.append(f"⏱ {total_time}")

    meta.append(f"🔢 {lessons_count} {plural_lessons(lessons_count)}")

    lines.append(" · ".join(meta))

    subjects = get_subjects_for_day(day)
    grouped = group_subjects(subjects)

    for subject_name, count in grouped.items():
        subject_safe = escape_html_text(subject_name)

        if count > 1:
            lines.append(f"{subject_safe} ×{count}")
        else:
            lines.append(subject_safe)

    return "\n".join(lines)


def format_week(schedule_data):
    days = get_relevant_week_days(schedule_data)

    lines = [
        f"📅 <b>Неделя · {escape_html_text(schedule_data['group'])}</b>",
        get_updated_line(schedule_data),
        "",
    ]

    if not days:
        lines.append("На ближайшую неделю занятий не найдено.")
        return ["\n".join(lines)]

    first_day = pretty_short_date(days[0]["date"])
    last_day = pretty_short_date(days[-1]["date"])
    total_lessons = sum(count_day_lessons(day) for day in days)

    lines.append(f"📆 <b>{first_day}–{last_day}</b> · всего <b>{total_lessons} {plural_lessons(total_lessons)}</b>")

    for day in days:
        lines.append("")
        lines.append(format_week_day_card(day))

    return split_long_message("\n".join(lines))


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
        "Бот расписания ВГЭК ✅\n\n"
        "Выбери кнопку ниже:\n\n"
        "📌 Сегодня — подробное расписание\n"
        "➡️ Завтра — подробное расписание\n"
        "📅 Неделя — обзор по дням\n"
        "🗓 По дате — краткая сводка на дату\n"
        "🔄 Обновить — обновить сегодня\n\n"
        f"Группа: <b>{DEFAULT_GROUP}</b>",
        parse_mode="HTML",
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

    await remove_loading_message(update, context, loading)

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

    await remove_loading_message(update, context, loading)

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
        "Собираю неделю…",
        reply_markup=MAIN_KEYBOARD
    )

    try:
        schedule = parse_group_schedule(DEFAULT_GROUP)
        messages = format_week(schedule)
    except Exception as error:
        messages = [f"❌ Ошибка:\n{escape_html_text(error)}"]

    await remove_loading_message(update, context, loading)

    for message in messages:
        await send_tracked_message(
            update,
            context,
            message,
            parse_mode="HTML",
            reply_markup=MAIN_KEYBOARD
        )


async def ask_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    WAITING_DATE_BY_CHAT.add(chat_id)

    await prepare_clean_response(update, context)

    await send_tracked_message(
        update,
        context,
        "🗓 Введи дату в формате <b>00.00</b>\n\n"
        "Например:\n"
        "<code>05.06</code>",
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD
    )


async def show_date_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    await prepare_clean_response(update, context)

    date_str = parse_user_short_date(user_text)

    if not date_str:
        await send_tracked_message(
            update,
            context,
            "Не понял дату.\n\n"
            "Нужно ввести в формате <b>00.00</b>\n"
            "Например: <code>05.06</code>",
            parse_mode="HTML",
            reply_markup=MAIN_KEYBOARD
        )
        return

    loading = await send_tracked_message(
        update,
        context,
        f"Ищу расписание на {escape_html_text(user_text)}…",
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD
    )

    try:
        schedule = parse_group_schedule(DEFAULT_GROUP)
        day = find_day_by_date(schedule, date_str)

        if not day:
            result = (
                f"🗓 <b>Сводка на {escape_html_text(user_text)}</b>\n"
                f"{get_updated_line(schedule)}\n\n"
                "Расписание на эту дату не найдено."
            )
        else:
            result = format_date_summary(schedule, day)

    except Exception as error:
        result = f"❌ Ошибка:\n{escape_html_text(error)}"

    await remove_loading_message(update, context, loading)

    await send_tracked_message(
        update,
        context,
        result,
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

    await remove_loading_message(update, context, loading)

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
    chat_id = update.effective_chat.id

    if chat_id in WAITING_DATE_BY_CHAT:
        WAITING_DATE_BY_CHAT.discard(chat_id)
        await show_date_summary(update, context, text)
        return

    if text == "📌 Сегодня":
        await today(update, context)
        return

    if text == "➡️ Завтра":
        await tomorrow(update, context)
        return

    if text == "📅 Неделя":
        await week(update, context)
        return

    if text == "🗓 По дате":
        await ask_date(update, context)
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

    print("Бот запущен.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
