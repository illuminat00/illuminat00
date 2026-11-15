"""
Telegram anonymous confession bot — v1
- Users send a confession, pick tags, admins approve/reject, bot posts to channel.
- Requires: pip install "aiogram>=3.7"

Environment variables:
  BOT_TOKEN         token from @BotFather
  CHANNEL_ID        @your_channel  (or -100xxxxxxxxxx)
  CHANNEL_USERNAME  @your_channel  (shown under each post)
  ADMIN_IDS         comma separated numeric IDs, e.g. 111111,222222
"""
import asyncio
import html
import logging
import os
import sqlite3
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ----------------------------- Config -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@HMD_YourNews")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@HMD_YourNews")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

TAGS = ["#همدان", "#دانشگاه_بوعلی", "#دانشگاه_آزاد"]
DAILY_LIMIT = 3          # max confessions per user per 24h
MAX_LEN = 3000           # max characters per confession

# Footer under every channel post. {id} = confession number, {channel} = @username
POST_FOOTER = (
    "┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
    "𓂃 #HMD_YourNews • #اعتراف_{id}\n"
    "🆔 {channel}"
)

BTN_NEW = "✍️ ثبت اعتراف"
BTN_HELP = "ℹ️ راهنما"

# Texts (edit freely)
WELCOME = (
    "سلام 👋\n"
    "به ربات اعتراف همدان خوش اومدی.\n"
    "اعترافت رو بفرست، تگش رو انتخاب کن و بعد از تایید، "
    "بدون اسم تو کانال منتشر می‌شه."
)
HELP = (
    "برای ثبت اعتراف روی «✍️ ثبت اعتراف» بزن، متنت رو بفرست "
    "و بعد تگ(های) مرتبط رو انتخاب کن.\n"
    "برای انصراف: /cancel"
)
CONFIRM = (
    "اعتراف شما با موفقیت ثبت گردید.\n"
    "      ‌◞ در اسرع وقت اعتراف شما در چنل قرار می‌گیرد.\n\n"
    "𓂃 Thanks for choosing us • 𝔖𝗂𝗇𝖽𝖾𝗋 ™"
)

# ----------------------------- Database ---------------------------
db = sqlite3.connect("confessions.db", check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript(
    """
    CREATE TABLE IF NOT EXISTS confessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        full_name TEXT,
        username TEXT,
        text TEXT NOT NULL,
        tags TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        channel_msg_id INTEGER,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS blocked(user_id INTEGER PRIMARY KEY);
    """
)
db.commit()


def is_blocked(user_id: int) -> bool:
    return db.execute("SELECT 1 FROM blocked WHERE user_id=?", (user_id,)).fetchone() is not None


def recent_count(user_id: int) -> int:
    since = int(time.time()) - 86400
    row = db.execute(
        "SELECT COUNT(*) c FROM confessions WHERE user_id=? AND created_at>?",
        (user_id, since),
    ).fetchone()
    return row["c"]


# ----------------------------- FSM --------------------------------
class Form(StatesGroup):
    waiting_text = State()
    waiting_tags = State()


router = Router()


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_NEW)], [KeyboardButton(text=BTN_HELP)]],
        resize_keyboard=True,
    )


def tags_kb(selected: list[int]):
    b = InlineKeyboardBuilder()
    for i, t in enumerate(TAGS):
        mark = "✅ " if i in selected else ""
        b.button(text=f"{mark}{t}", callback_data=f"tag:{i}")
    b.button(text="📨 ثبت نهایی", callback_data="tags_done")
    b.button(text="❌ انصراف", callback_data="tags_cancel")
    b.adjust(1)
    return b.as_markup()


def admin_kb(cid: int):
    b = InlineKeyboardBuilder()
    b.button(text="✅ تایید", callback_data=f"adm:ok:{cid}")
    b.button(text="❌ رد", callback_data=f"adm:no:{cid}")
    b.button(text="🚫 بلاک کاربر", callback_data=f"adm:ban:{cid}")
    b.adjust(2, 1)
    return b.as_markup()


# ----------------------------- User flow --------------------------
@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    if is_blocked(m.from_user.id):
        return
    await m.answer(WELCOME, reply_markup=main_menu())


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("لغو شد.", reply_markup=main_menu())


@router.message(F.text == BTN_HELP)
async def help_(m: Message):
    await m.answer(HELP)


@router.message(F.text == BTN_NEW)
async def new_confession(m: Message, state: FSMContext):
    if is_blocked(m.from_user.id):
        return
    if recent_count(m.from_user.id) >= DAILY_LIMIT:
        await m.answer("به سقف تعداد اعتراف امروز رسیدی. فردا دوباره امتحان کن 🌙")
        return
    await state.set_state(Form.waiting_text)
    await m.answer(f"متن اعترافت رو بفرست ✍️\n(حداکثر {MAX_LEN} کاراکتر)")


@router.message(Form.waiting_text, F.text)
async def got_text(m: Message, state: FSMContext):
    if len(m.text) > MAX_LEN:
        await m.answer(f"متن خیلی بلنده ({len(m.text)} کاراکتر). لطفاً کوتاه‌ترش کن.")
        return
    await state.update_data(text=m.text, selected=[])
    await state.set_state(Form.waiting_tags)
    await m.answer("تگ(های) مرتبط رو انتخاب کن 👇", reply_markup=tags_kb([]))


@router.message(Form.waiting_text)
async def not_text(m: Message):
    await m.answer("فعلاً فقط متن قبول می‌کنم. لطفاً اعترافت رو به صورت متن بفرست.")


@router.callback_query(Form.waiting_tags, F.data.startswith("tag:"))
async def toggle_tag(c: CallbackQuery, state: FSMContext):
    idx = int(c.data.split(":")[1])
    data = await state.get_data()
    selected: list[int] = data.get("selected", [])
    if idx in selected:
        selected.remove(idx)
    else:
        selected.append(idx)
    await state.update_data(selected=selected)
    await c.message.edit_reply_markup(reply_markup=tags_kb(selected))
    await c.answer()


@router.callback_query(Form.waiting_tags, F.data == "tags_cancel")
async def tags_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("لغو شد.")
    await c.answer()


@router.callback_query(Form.waiting_tags, F.data == "tags_done")
async def tags_done(c: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    selected: list[int] = sorted(data.get("selected", []))
    if not selected:
        await c.answer("حداقل یک تگ انتخاب کن.", show_alert=True)
        return

    user = c.from_user
    if is_blocked(user.id):
        await state.clear()
        return
    if recent_count(user.id) >= DAILY_LIMIT:
        await state.clear()
        await c.message.edit_text("به سقف تعداد اعتراف امروز رسیدی.")
        await c.answer()
        return

    tags = " ".join(TAGS[i] for i in selected)
    cur = db.execute(
        "INSERT INTO confessions(user_id, full_name, username, text, tags, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (user.id, user.full_name, user.username, data["text"], tags, int(time.time())),
    )
    db.commit()
    cid = cur.lastrowid
    await state.clear()

    # Send to admins
    admin_text = (
        f"📥 <b>اعتراف جدید #{cid}</b>\n"
        f"👤 نام: {html.escape(user.full_name)}\n"
        f"🔗 یوزرنیم: {'@' + user.username if user.username else 'ندارد'}\n"
        f"🆔 آیدی: <code>{user.id}</code>\n\n"
        f"{html.escape(data['text'])}\n\n"
        f"🏷 {tags}"
    )
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin, admin_text, reply_markup=admin_kb(cid))
        except Exception as e:  # admin may not have started the bot
            logging.warning("Could not notify admin %s: %s", admin, e)

    await c.message.edit_text("✅ ثبت شد.")
    await bot.send_message(user.id, CONFIRM, reply_markup=main_menu())
    await c.answer()


# ----------------------------- Admin actions ----------------------
@router.callback_query(F.data.startswith("adm:"))
async def admin_action(c: CallbackQuery, bot: Bot):
    if c.from_user.id not in ADMIN_IDS:
        await c.answer("دسترسی نداری.", show_alert=True)
        return

    _, action, cid_s = c.data.split(":")
    cid = int(cid_s)
    row = db.execute("SELECT * FROM confessions WHERE id=?", (cid,)).fetchone()
    if row is None:
        await c.answer("پیدا نشد.", show_alert=True)
        return
    if row["status"] != "pending":
        await c.answer("این اعتراف قبلاً بررسی شده.", show_alert=True)
        return

    admin_name = html.escape(c.from_user.full_name)

    if action == "ok":
        post = (
            f"{html.escape(row['text'])}\n\n"
            f"{row['tags']}\n\n"
            f"{POST_FOOTER.format(id=cid, channel=html.escape(CHANNEL_USERNAME))}"
        )
        try:
            sent = await bot.send_message(CHANNEL_ID, post)
        except Exception as e:
            await c.answer(f"خطا در ارسال به کانال: {e}", show_alert=True)
            return
        db.execute(
            "UPDATE confessions SET status='approved', channel_msg_id=? WHERE id=?",
            (sent.message_id, cid),
        )
        db.commit()
        note = "🎉 اعترافت تایید شد و تو کانال منتشر شد."
        if CHANNEL_USERNAME.startswith("@"):
            note += f"\nhttps://t.me/{CHANNEL_USERNAME[1:]}/{sent.message_id}"
        await _safe_send(bot, row["user_id"], note)
        verdict = f"✅ تایید شد توسط {admin_name}"

    elif action == "no":
        db.execute("UPDATE confessions SET status='rejected' WHERE id=?", (cid,))
        db.commit()
        await _safe_send(bot, row["user_id"], "متاسفانه اعتراف شما تایید نشد.")
        verdict = f"❌ رد شد توسط {admin_name}"

    else:  # ban
        db.execute("UPDATE confessions SET status='rejected' WHERE id=?", (cid,))
        db.execute("INSERT OR IGNORE INTO blocked(user_id) VALUES(?)", (row["user_id"],))
        db.commit()
        verdict = f"🚫 رد و کاربر بلاک شد توسط {admin_name}"

    await c.message.edit_text(c.message.html_text + f"\n\n{verdict}", reply_markup=None)
    await c.answer()


async def _safe_send(bot: Bot, user_id: int, text: str):
    try:
        await bot.send_message(user_id, text)
    except Exception as e:
        logging.warning("Could not message user %s: %s", user_id, e)


# ----------------------------- Main -------------------------------
async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN or not CHANNEL_ID or not ADMIN_IDS:
        raise SystemExit("Set BOT_TOKEN, CHANNEL_ID and ADMIN_IDS environment variables.")
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
