import asyncio
import logging
import math
import os
import random
import sqlite3
import string
from datetime import datetime
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "teacher_checking_bot.db"
OUTPUT_DIR = BASE_DIR / "generated"
OUTPUT_DIR.mkdir(exist_ok=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "7971534785:AAG463XSpjLe8v1XkrAC2QQOayO9K6Cs2js")
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
logging.basicConfig(level=logging.INFO)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            teacher_name TEXT,
            test_name TEXT NOT NULL,
            subject TEXT NOT NULL,
            test_code TEXT UNIQUE NOT NULL,
            answer_key TEXT NOT NULL,
            total_questions INTEGER NOT NULL,
            is_finished INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            student_user_id INTEGER NOT NULL,
            student_name TEXT NOT NULL,
            answers TEXT NOT NULL,
            correct_count INTEGER NOT NULL,
            percent REAL NOT NULL,
            submitted_at TEXT NOT NULL,
            UNIQUE(test_id, student_user_id)
        )
        """
    )
    conn.commit()
    conn.close()


def normalize_answers(text: str) -> str:
    text = (text or "").upper().replace("YO'Q", "").replace(" ", "")
    return "".join(ch for ch in text if ch in "ABCD")


def generate_code(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def create_unique_code() -> str:
    conn = get_conn()
    cur = conn.cursor()
    while True:
        code = generate_code()
        cur.execute("SELECT 1 FROM tests WHERE test_code = ?", (code,))
        if not cur.fetchone():
            conn.close()
            return code


def teacher_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Test yaratish")],
            [KeyboardButton(text="📋 Mening testlarim"), KeyboardButton(text="🏁 Testni yakunlash")],
            [KeyboardButton(text="👤 O'quvchi rejimi")],
        ],
        resize_keyboard=True,
    )


def student_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Test topshirish")],
            [KeyboardButton(text="👨‍🏫 O'qituvchi rejimi")],
        ],
        resize_keyboard=True,
    )


class CreateTestState(StatesGroup):
    waiting_for_test_name = State()
    waiting_for_subject = State()
    waiting_for_answer_key = State()


class SubmitState(StatesGroup):
    waiting_for_student_name = State()
    waiting_for_test_code = State()
    waiting_for_answers = State()


class FinishState(StatesGroup):
    waiting_for_code = State()


def register_fonts():
    regular_candidates = [
        "C:/Windows/Fonts/times.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ]
    bold_candidates = [
        "C:/Windows/Fonts/timesbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    ]
    italic_candidates = [
        "C:/Windows/Fonts/timesi.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    ]

    for name, candidates in [
        ("CertRegular", regular_candidates),
        ("CertBold", bold_candidates),
        ("CertItalic", italic_candidates),
    ]:
        for path in candidates:
            if os.path.exists(path):
                try:
                    pdfmetrics.registerFont(TTFont(name, path))
                    break
                except Exception:
                    pass


def pick_font(name: str, fallback: str) -> str:
    try:
        pdfmetrics.getFont(name)
        return name
    except Exception:
        return fallback


def draw_centered_text(c: canvas.Canvas, text: str, y: float, font_name: str, size: int, color=colors.black):
    c.setFillColor(color)
    c.setFont(font_name, size)
    width, _ = landscape(A4)
    text_width = c.stringWidth(text, font_name, size)
    c.drawString((width - text_width) / 2, y, text)


def fit_text_size(c: canvas.Canvas, text: str, font_name: str, max_size: int, min_size: int, max_width: float) -> int:
    size = max_size
    while size > min_size and c.stringWidth(text, font_name, size) > max_width:
        size -= 1
    return size


def draw_ribbon(c: canvas.Canvas, center_x: float, center_y: float, scale: float = 1.0):
    gold = colors.HexColor("#d9b24c")
    dark = colors.HexColor("#1f2d3d")
    c.setFillColor(dark)
    c.setStrokeColor(dark)
    c.wedge(center_x - 12 * scale, center_y - 34 * scale, center_x + 2 * scale, center_y - 2 * scale, 240, 300, fill=1, stroke=0)
    c.wedge(center_x - 2 * scale, center_y - 34 * scale, center_x + 12 * scale, center_y - 2 * scale, 240, 300, fill=1, stroke=0)
    c.setFillColor(gold)
    c.circle(center_x, center_y, 14 * scale, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#27374d"))
    c.circle(center_x, center_y, 9 * scale, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#f7e296"))
    c.circle(center_x, center_y, 6 * scale, fill=0, stroke=1)


def draw_corner_swirl(c: canvas.Canvas, x: float, y: float, flip_x: int = 1, flip_y: int = 1):
    c.saveState()
    c.translate(x, y)
    c.scale(flip_x, flip_y)
    c.setStrokeColor(colors.HexColor("#1f2d3d"))
    c.setLineWidth(1.1)
    c.arc(-10, -10, 30, 30, startAng=0, extent=270)
    c.arc(8, 8, 34, 34, startAng=90, extent=270)
    c.arc(26, 26, 42, 42, startAng=180, extent=240)
    c.restoreState()


def draw_top_ornament(c: canvas.Canvas, width: float, top_y: float, font_name: str):
    c.setStrokeColor(colors.HexColor("#596e79"))
    c.setLineWidth(1.2)
    c.line(95 * mm, top_y, width - 95 * mm, top_y)
    c.line(95 * mm, top_y - 18, width - 95 * mm, top_y - 18)
    draw_centered_text(c, "❦", top_y + 2, font_name, 20, colors.HexColor("#4f5d75"))
    draw_centered_text(c, "❧", top_y - 22, font_name, 18, colors.HexColor("#4f5d75"))


def create_certificates_pdf(test_row, results_rows):
    register_fonts()
    width, height = landscape(A4)
    file_name = OUTPUT_DIR / f"certificates_{test_row['test_code']}.pdf"
    c = canvas.Canvas(str(file_name), pagesize=landscape(A4))

    title_font = pick_font("CertBold", "Helvetica-Bold")
    body_font = pick_font("CertRegular", "Times-Roman")
    italic_font = pick_font("CertItalic", "Times-Italic")
    bold_font = pick_font("CertBold", "Helvetica-Bold")

    created_date = datetime.now().strftime("%d.%m.%Y")

    for rank, row in enumerate(results_rows, start=1):
        # Background gradient-like layers
        c.setFillColor(colors.HexColor("#9fe4ff"))
        c.rect(0, 0, width, height, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#c8f0ff"))
        c.circle(0, height, 160 * mm, fill=1, stroke=0)
        c.circle(width, height, 160 * mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#d8f5ff"))
        c.circle(width / 2, height / 2, 120 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.circle(width / 2, height / 2, 90 * mm, fill=1, stroke=0)

        # Outer and inner borders
        c.setStrokeColor(colors.HexColor("#0a6cae"))
        c.setLineWidth(3)
        c.roundRect(12 * mm, 12 * mm, width - 24 * mm, height - 24 * mm, 8 * mm, stroke=1, fill=0)
        c.setLineWidth(1.2)
        c.roundRect(18 * mm, 18 * mm, width - 36 * mm, height - 36 * mm, 6 * mm, stroke=1, fill=0)

        # Corner ornaments
        draw_corner_swirl(c, 24 * mm, height - 24 * mm, 1, 1)
        draw_corner_swirl(c, width - 24 * mm, height - 24 * mm, -1, 1)
        draw_corner_swirl(c, 24 * mm, 24 * mm, 1, -1)
        draw_corner_swirl(c, width - 24 * mm, 24 * mm, -1, -1)

        # Top decorative lines and ribbons
        draw_top_ornament(c, width, height - 34 * mm, title_font)
        draw_ribbon(c, 42 * mm, height - 58 * mm, 1.5)
        draw_ribbon(c, width - 42 * mm, height - 58 * mm, 1.5)

        # Title block
        draw_centered_text(c, "Sertifikat", height - 56 * mm, title_font, 42, colors.HexColor("#0a2e9a"))
        draw_centered_text(c, "Online test qatnashuvchisi", height - 74 * mm, italic_font, 17, colors.HexColor("#202020"))

        # Main body layout
        left = 55 * mm
        right = width - 55 * mm
        line_start = left + 38 * mm
        y1 = height - 108 * mm
        y2 = height - 130 * mm
        y3 = height - 152 * mm
        y4 = height - 174 * mm

        c.setFont(body_font, 18)
        c.setFillColor(colors.black)
        c.drawString(left, y1, "Hurmatli")
        c.line(line_start, y1 - 2, right - 20 * mm, y1 - 2)
        name_font_size = fit_text_size(c, row["student_name"], bold_font, 24, 16, (right - 25 * mm) - line_start)
        c.setFont(bold_font, name_font_size)
        c.drawString(line_start + 3 * mm, y1 + 1, row["student_name"])

        c.setFont(body_font, 18)
        c.line(left, y2 - 2, left + 72 * mm, y2 - 2)
        subject_font_size = fit_text_size(c, test_row["subject"], bold_font, 20, 15, 68 * mm)
        c.setFont(bold_font, subject_font_size)
        c.drawString(left + 2 * mm, y2 + 1, test_row["subject"])
        c.setFont(body_font, 18)
        c.drawString(left + 76 * mm, y2, "fanidan o'tkazilgan online testda")

        score_text = f"{row['correct_count']} ta"
        percent_text = f"{int(round(row['percent']))}%"
        c.line(left, y3 - 2, left + 30 * mm, y3 - 2)
        c.line(left + 48 * mm, y3 - 2, left + 76 * mm, y3 - 2)
        c.setFont(bold_font, 19)
        c.drawString(left + 2 * mm, y3 + 1, score_text)
        c.drawString(left + 50 * mm, y3 + 1, percent_text)
        c.setFont(body_font, 18)
        c.drawString(left + 82 * mm, y3, "natija ko'rsatib faol qatnashganligi")

        c.setFont(body_font, 18)
        c.drawString(left, y4, "uchun")
        c.line(left + 20 * mm, y4 - 2, left + 84 * mm, y4 - 2)
        teacher_label = test_row["teacher_name"] or "o'qituvchi"
        teacher_font_size = fit_text_size(c, teacher_label, bold_font, 18, 13, 60 * mm)
        c.setFont(bold_font, teacher_font_size)
        c.drawString(left + 22 * mm, y4 + 1, teacher_label)
        c.setFont(body_font, 18)
        c.drawString(left + 88 * mm, y4, "tomonidan ushbu sertifikat bilan")
        c.drawString(left, y4 - 17, "taqdirlanadi.")

        # Rank badge
        badge_x = width / 2
        badge_y = 48 * mm
        c.setFillColor(colors.HexColor("#0f4c81"))
        c.roundRect(badge_x - 30 * mm, badge_y - 7 * mm, 60 * mm, 14 * mm, 4 * mm, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont(bold_font, 18)
        rank_label = f"{rank}-o'rin"
        rank_width = c.stringWidth(rank_label, bold_font, 18)
        c.drawString(badge_x - rank_width / 2, badge_y - 1.5 * mm, rank_label)

        # Footer details
        c.setFillColor(colors.black)
        c.setFont(body_font, 16)
        c.drawString(40 * mm, 24 * mm, "Sana:")
        c.line(56 * mm, 23 * mm, 105 * mm, 23 * mm)
        c.drawString(58 * mm, 26 * mm, created_date)

        c.drawString(width / 2 - 30 * mm, 24 * mm, "Test:")
        c.line(width / 2 - 12 * mm, 23 * mm, width / 2 + 72 * mm, 23 * mm)
        c.setFont(bold_font, 14)
        short_test_name = test_row["test_name"]
        if len(short_test_name) > 28:
            short_test_name = short_test_name[:25] + "..."
        c.drawString(width / 2 - 10 * mm, 26 * mm, short_test_name)

        c.setFont(body_font, 16)
        c.line(width - 125 * mm, 23 * mm, width - 35 * mm, 23 * mm)
        c.drawString(width - 117 * mm, 26 * mm, f"Kod: {test_row['test_code']}")

        c.showPage()

    c.save()
    return file_name


def fetch_teacher_tests(teacher_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tests WHERE teacher_id = ? ORDER BY id DESC", (teacher_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def compute_rank(results_rows, student_user_id: int) -> int:
    for idx, row in enumerate(results_rows, start=1):
        if row["student_user_id"] == student_user_id:
            return idx
    return len(results_rows)


init_db()

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    text = (
        "<b>Test tekshiruvchi bot</b>\n\n"
        "O'qituvchi test nomi va javoblar kalitini kiritadi.\n"
        "O'quvchi kod orqali javob yuboradi.\n"
        "Oxirida o'qituvchi bezakli bitta PDF ichida barcha sertifikatlarni oladi."
    )
    await message.answer(text, reply_markup=teacher_menu())


@dp.message(F.text == "👨‍🏫 O'qituvchi rejimi")
async def switch_teacher(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("O'qituvchi menyusi ochildi.", reply_markup=teacher_menu())


@dp.message(F.text == "👤 O'quvchi rejimi")
async def switch_student(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("O'quvchi menyusi ochildi.", reply_markup=student_menu())


@dp.message(F.text == "➕ Test yaratish")
async def create_test_start(message: Message, state: FSMContext):
    await state.set_state(CreateTestState.waiting_for_test_name)
    await message.answer("Test nomini yuboring.", reply_markup=ReplyKeyboardRemove())


@dp.message(CreateTestState.waiting_for_test_name)
async def get_test_name(message: Message, state: FSMContext):
    await state.update_data(test_name=(message.text or "").strip())
    await state.set_state(CreateTestState.waiting_for_subject)
    await message.answer("Fan nomini yuboring. Masalan: Matematika")


@dp.message(CreateTestState.waiting_for_subject)
async def get_subject(message: Message, state: FSMContext):
    await state.update_data(subject=(message.text or "").strip())
    await state.set_state(CreateTestState.waiting_for_answer_key)
    await message.answer(
        "Javoblar kalitini yuboring.\nMasalan: <code>ABCDABCD</code>\nFaqat A, B, C, D harflari ishlatiladi."
    )


@dp.message(CreateTestState.waiting_for_answer_key)
async def get_answer_key(message: Message, state: FSMContext):
    answer_key = normalize_answers(message.text or "")
    if len(answer_key) == 0:
        await message.answer("Kalit noto'g'ri. Faqat A, B, C, D harflaridan iborat kalit yuboring.")
        return

    data = await state.get_data()
    code = create_unique_code()
    teacher_name = message.from_user.full_name

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO tests (teacher_id, teacher_name, test_name, subject, test_code, answer_key, total_questions, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message.from_user.id,
            teacher_name,
            data["test_name"],
            data["subject"],
            code,
            answer_key,
            len(answer_key),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer(
        f"<b>Test yaratildi.</b>\n\n"
        f"Test nomi: {data['test_name']}\n"
        f"Fan: {data['subject']}\n"
        f"Savollar soni: {len(answer_key)}\n"
        f"Test kodi: <code>{code}</code>\n\n"
        f"Shu kodni o'quvchilarga yuborasiz.",
        reply_markup=teacher_menu(),
    )


@dp.message(F.text == "📋 Mening testlarim")
async def my_tests(message: Message):
    rows = fetch_teacher_tests(message.from_user.id)
    if not rows:
        await message.answer("Sizda hali test yo'q.")
        return

    lines = ["<b>Siz yaratgan testlar:</b>"]
    for row in rows[:20]:
        status = "Yakunlangan" if row["is_finished"] else "Aktiv"
        lines.append(
            f"\n• {row['test_name']}\n"
            f"  Fan: {row['subject']}\n"
            f"  Kod: <code>{row['test_code']}</code>\n"
            f"  Savollar: {row['total_questions']}\n"
            f"  Holat: {status}"
        )
    await message.answer("\n".join(lines))


@dp.message(F.text == "📝 Test topshirish")
async def submit_start(message: Message, state: FSMContext):
    await state.set_state(SubmitState.waiting_for_student_name)
    await message.answer("Ism familiyangizni yuboring.", reply_markup=ReplyKeyboardRemove())


@dp.message(SubmitState.waiting_for_student_name)
async def submit_name(message: Message, state: FSMContext):
    await state.update_data(student_name=(message.text or "").strip())
    await state.set_state(SubmitState.waiting_for_test_code)
    await message.answer("Test kodini yuboring.")


@dp.message(SubmitState.waiting_for_test_code)
async def submit_code(message: Message, state: FSMContext):
    code = (message.text or "").strip().upper()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tests WHERE test_code = ? AND is_finished = 0", (code,))
    test = cur.fetchone()
    conn.close()

    if not test:
        await message.answer("Bunday aktiv test topilmadi. Kodni tekshirib qayta yuboring.")
        return

    await state.update_data(test_code=code, test_id=test["id"], total_questions=test["total_questions"])
    await state.set_state(SubmitState.waiting_for_answers)
    await message.answer(
        f"Javoblaringizni yuboring.\nMasalan: <code>ABCDABCD</code>\nBu testda {test['total_questions']} ta savol bor."
    )


@dp.message(SubmitState.waiting_for_answers)
async def submit_answers(message: Message, state: FSMContext):
    data = await state.get_data()
    answers = normalize_answers(message.text or "")
    total_questions = int(data["total_questions"])

    if len(answers) != total_questions:
        await message.answer(
            f"Javoblar soni mos emas.\n"
            f"Siz yuborgan javoblar: {len(answers)} ta\n"
            f"Kerakligi: {total_questions} ta"
        )
        return

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tests WHERE id = ?", (data["test_id"],))
    test = cur.fetchone()

    cur.execute(
        "SELECT 1 FROM results WHERE test_id = ? AND student_user_id = ?",
        (data["test_id"], message.from_user.id),
    )
    if cur.fetchone():
        conn.close()
        await state.clear()
        await message.answer("Siz bu testni avval topshirgansiz.", reply_markup=student_menu())
        return

    correct = sum(1 for a, b in zip(answers, test["answer_key"]) if a == b)
    percent = round(correct * 100 / total_questions, 2)

    cur.execute(
        """
        INSERT INTO results (test_id, student_user_id, student_name, answers, correct_count, percent, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["test_id"],
            message.from_user.id,
            data["student_name"],
            answers,
            correct,
            percent,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()

    cur.execute(
        """
        SELECT student_user_id, student_name, correct_count, percent, submitted_at
        FROM results
        WHERE test_id = ?
        ORDER BY correct_count DESC, percent DESC, submitted_at ASC
        """,
        (data["test_id"],),
    )
    ranking = cur.fetchall()
    conn.close()

    rank = compute_rank(ranking, message.from_user.id)

    await state.clear()
    await message.answer(
        f"<b>Natijangiz tayyor.</b>\n\n"
        f"Test: {test['test_name']}\n"
        f"To'g'ri javob: {correct}/{total_questions}\n"
        f"Foiz: {percent}%\n"
        f"Hozirgi o'rningiz: {rank}-o'rin\n\n"
        f"Sertifikat keyin o'qituvchiga bitta PDF ichida yuboriladi.",
        reply_markup=student_menu(),
    )


@dp.message(F.text == "🏁 Testni yakunlash")
async def finish_start(message: Message, state: FSMContext):
    await state.set_state(FinishState.waiting_for_code)
    await message.answer("Yakunlamoqchi bo'lgan test kodini yuboring.", reply_markup=ReplyKeyboardRemove())


@dp.message(FinishState.waiting_for_code)
async def finish_test(message: Message, state: FSMContext):
    code = (message.text or "").strip().upper()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM tests WHERE test_code = ? AND teacher_id = ?",
        (code, message.from_user.id),
    )
    test = cur.fetchone()

    if not test:
        conn.close()
        await message.answer("Bunday test topilmadi yoki bu test sizga tegishli emas.")
        return

    cur.execute(
        """
        SELECT *
        FROM results
        WHERE test_id = ?
        ORDER BY correct_count DESC, percent DESC, submitted_at ASC
        """,
        (test["id"],),
    )
    results = cur.fetchall()

    if not results:
        conn.close()
        await state.clear()
        await message.answer("Bu testga hali hech kim javob topshirmagan.", reply_markup=teacher_menu())
        return

    cur.execute("UPDATE tests SET is_finished = 1 WHERE id = ?", (test["id"],))
    conn.commit()
    conn.close()

    pdf_path = create_certificates_pdf(test, results)

    avg_percent = round(sum(r["percent"] for r in results) / len(results), 2)
    lines = [
        f"<b>Test yakunlandi: {test['test_name']}</b>",
        f"Fan: {test['subject']}",
        f"Kod: <code>{test['test_code']}</code>",
        f"Jami qatnashganlar: {len(results)} ta",
        f"O'rtacha natija: {avg_percent}%",
        "\n<b>Reyting:</b>",
    ]
    for idx, row in enumerate(results, start=1):
        lines.append(f"{idx}. {row['student_name']} — {row['correct_count']}/{test['total_questions']} — {row['percent']}%")

    await message.answer("\n".join(lines))
    await message.answer_document(
        FSInputFile(str(pdf_path), filename=pdf_path.name),
        caption="Bezakli sertifikatlar bitta PDF ichida tayyor bo'ldi.",
        reply_markup=teacher_menu(),
    )
    await state.clear()


@dp.message()
async def fallback(message: Message):
    await message.answer("Buyruqni menyudan tanlang. /start bosib bosh menyuga qaytishingiz mumkin.")


import asyncio
from aiogram import Bot

async def main():
    # TOKEN tekshirish
    if BOT_TOKEN == "7971534785:AAG463XSpjLe8v1XkrAC2QQOayO9K6Cs2js":
        raise RuntimeError("BOT_TOKEN ni Render'da Environment Variables ga qo'ying.")

    # Bot yaratish
    bot = Bot(token=BOT_TOKEN)

    print("Bot ishga tushdi...")

    # Polling boshlash
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
