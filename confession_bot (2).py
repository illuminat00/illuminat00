"""
HMD YourNews — Telegram anonymous news bot (v3)

Env: BOT_TOKEN, CHANNEL_ID, CHANNEL_USERNAME, ADMIN_IDS (comma separated)
     DATABASE_URL (optional: Postgres; without it a SQLite file at DB_PATH is used)
     DB_PATH      (optional: SQLite file; default /data/confessions.db if /data exists)
     PORT         (optional: starts a tiny health-check web server)
Requires: pip install "aiogram>=3.28"
"""
import asyncio
import html
import logging
import os
import re

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db import Database

# ----------------------------- Config -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@HMD_YourNews")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@HMD_YourNews")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_PATH = os.getenv("DB_PATH", "/data/confessions.db" if os.path.isdir("/data") else "confessions.db")

TAGS = ["#همدان", "#دانشگاه_بوعلی", "#دانشگاه_آزاد"]
MAX_CUSTOM = 2           # max custom tags per news
MAX_LEN = 3000           # max characters per news

# Footer under every channel post. {id} = news number, {channel} = @username
POST_FOOTER = (
    "┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
    "𓂃 #HMD_YourNews • #نیوز_{id}\n"
    "🆔 {channel}"
)

BTN_NEW = "✍️ ثبت نیوز"
BTN_HELP = "ℹ️ راهنما"
BTN_RULES = "📜 قوانین"
BTN_ACCEPT = "✅ قوانین را می‌پذیرم"

# ----------------------------- Texts ------------------------------
RULES = (
    "📜 <b>قوانین ربات و کانال نیوز همدان</b>\n\n"
    "۱. <b>احترام:</b> نظر و تجربه‌ات رو آزادانه بنویس، فقط به افراد، قومیت‌ها، "
    "مذاهب، دانشگاه‌ها یا گروه‌ها توهین و تحقیر نکن.\n\n"
    "۲. <b>حریم خصوصی:</b> نوشتن اسم یا آیدی خودت کاملاً آزاده. فقط اطلاعات خصوصی "
    "دیگران (مثل شماره، آدرس یا عکس) رو بدون رضایتشون منتشر نکن.\n\n"
    "۳. <b>محتوای ممنوع:</b> تبلیغات، لینک و معرفی کانال‌های دیگه، محتوای مستهجن، "
    "تهدید و خشونت و ترویج مواد مخدر منتشر نمی‌شه.\n\n"
    "۴. <b>تهمت و شایعه:</b> کسی رو بدون دلیل و مدرک به چیزی متهم نکن. "
    "مسئولیت درستی نیوز با فرستنده‌ست.\n\n"
    "۵. <b>اسپم:</b> نیوز تکراری یا بی‌معنی نفرست.\n\n"
    "۶. <b>تگ‌ها:</b> برای نیوزت تگ مرتبط انتخاب کن. تگ دلخواه هم می‌تونی بسازی، "
    "ولی تگ توهین‌آمیز یا تبلیغاتی قبول نمی‌شه.\n\n"
    "۷. <b>انتشار:</b> ارسال نیوز تضمین انتشار نیست. ادمین‌ها حق دارن هر نیوز رو "
    "تایید یا رد کنن.\n\n"
    "۸. <b>اسم فرستنده:</b> نیوز بدون ذکر نام فرستنده تو کانال منتشر می‌شه "
    "(مگه اینکه خودت تو متن بنویسی).\n\n"
    "۹. <b>رعایت قوانین:</b> تخلف‌های تکراری ممکنه به بلاک شدن ختم بشه.\n\n"
    "با زدن دکمه‌ی زیر قوانین رو می‌پذیری 👇"
)

WELCOME = (
    "سلام 👋\n"
    "به ربات «نیوز همدان» خوش اومدی!\n"
    "نیوزت رو بفرست، تگش رو انتخاب کن و بعد از تایید ادمین، "
    "بدون ذکر نام فرستنده تو کانال منتشر می‌شه."
)

HELP = (
    "📖 <b>راهنمای ربات نیوز همدان</b>\n\n"
    "<b>چطور نیوز بفرستم؟</b>\n"
    f"۱. روی «{BTN_NEW}» بزن.\n"
    f"۲. متن نیوزت رو بفرست (حداکثر {MAX_LEN} کاراکتر).\n"
    "۳. تگ(های) مرتبط رو انتخاب کن. اگه تگ مناسبی نبود، با «➕ تگ دلخواه» "
    "خودت تگ بساز.\n"
    "۴. روی «📨 ثبت نهایی» بزن.\n\n"
    "<b>بعد از ثبت چی می‌شه؟</b>\n"
    f"نیوزت برای ادمین‌ها می‌ره. بعد از تایید، بدون نام فرستنده تو کانال "
    f"{html.escape(CHANNEL_USERNAME)} منتشر می‌شه و لینک پستش برات فرستاده می‌شه. "
    "اگه تایید نشد هم بهت خبر می‌دیم.\n\n"
    "<b>نکته‌ها</b>\n"
    "• فعلاً فقط متن قبول می‌شه (عکس و فایل نه).\n"
    "• محدودیتی برای تعداد نیوز نداری.\n"
    f"• تا {MAX_CUSTOM} تگ دلخواه می‌تونی اضافه کنی؛ برای حذفش روی همون تگ بزن.\n"
    "• نوشتن اسم یا آیدی خودت تو متن آزاده.\n\n"
    "<b>دستورها</b>\n"
    "/start — شروع و منوی اصلی\n"
    "/help — همین راهنما\n"
    "/rules — قوانین\n"
    "/cancel — لغو ارسال نیوز"
)

CONFIRM = (
    "نیوز شما با موفقیت ثبت گردید.\n"
    "      \u200c◞ در اسرع وقت نیوز شما در چنل قرار می‌گیرد.\n\n"
    "𓂃 Thanks for choosing us • HMD\u200c YourNews"
)

# ----------------------------- Database ---------------------------
db = Database()  # connected in main()


# ----------------------------- Helpers ----------------------------
class Form(StatesGroup):
    waiting_text = State()
    waiting_tags = State()
    waiting_custom_tag = State()


router = Router()


def clean_tag(raw: str):
    """Turn free text into a safe hashtag, or None if invalid."""
    t = raw.strip().lstrip("#").replace("\u200c", "_")
    t = re.sub(r"[\s\-]+", "_", t)
    t = re.sub(r"[^\w]", "", t)
    t = re.sub(r"_+", "_", t).strip("_")
    if not 2 <= len(t) <= 30:
        return None
    return "#" + t


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NEW, style="primary")],
            [KeyboardButton(text=BTN_HELP), KeyboardButton(text=BTN_RULES)],
        ],
        resize_keyboard=True,
    )


def tags_kb(selected: list[int], custom: list[str]):
    b = InlineKeyboardBuilder()
    for i, t in enumerate(TAGS):
        mark = "✅ " if i in selected else ""
        b.button(
            text=f"{mark}{t}",
            callback_data=f"tag:{i}",
            style="success" if i in selected else "primary",
        )
    for i, t in enumerate(custom):
        b.button(text=f"✅ {t}  ✖️", callback_data=f"ctag_del:{i}", style="success")
    if len(custom) < MAX_CUSTOM:
        b.button(text="➕ تگ دلخواه", callback_data="tag_custom")
    b.button(text="📨 ثبت نهایی", callback_data="tags_done", style="success")
    b.button(text="❌ انصراف", callback_data="tags_cancel", style="danger")
    b.adjust(1)
    return b.as_markup()


def admin_kb(cid: int):
    b = InlineKeyboardBuilder()
    b.button(text="✅ تایید", callback_data=f"adm:ok:{cid}", style="success")
    b.button(text="❌ رد", callback_data=f"adm:no:{cid}", style="danger")
    b.button(text="🚫 بلاک کاربر", callback_data=f"adm:ban:{cid}")
    b.adjust(2, 1)
    return b.as_markup()


async def refresh_kb(bot: Bot, chat_id: int, data: dict):
    """Re-draw the tag keyboard on the original tag message."""
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=data["kb_msg"],
            reply_markup=tags_kb(data.get("selected", []), data.get("custom", [])),
        )
    except Exception as e:
        logging.warning("Could not refresh keyboard: %s", e)


# ----------------------------- Start / rules / help ---------------
async def send_welcome(m: Message):
    await m.answer(WELCOME, reply_markup=main_menu())
    await m.answer(HELP)


async def send_rules(m: Message, with_button: bool = True):
    if with_button:
        b = InlineKeyboardBuilder()
        b.button(text=BTN_ACCEPT, callback_data="rules_ok", style="success")
        await m.answer(RULES, reply_markup=b.as_markup())
    else:
        await m.answer(RULES)


@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    if await db.is_blocked(m.from_user.id):
        return
    if not await db.has_accepted(m.from_user.id):
        await send_rules(m)
        return
    await send_welcome(m)


@router.callback_query(F.data == "rules_ok")
async def rules_ok(c: CallbackQuery):
    if await db.is_blocked(c.from_user.id):
        await c.answer()
        return
    await db.accept(c.from_user.id)
    await c.message.edit_reply_markup(reply_markup=None)
    await send_welcome(c.message)
    await c.answer("✅ قوانین تایید شد")


@router.message(Command("rules"))
@router.message(F.text == BTN_RULES)
async def rules_cmd(m: Message):
    await send_rules(m, with_button=not await db.has_accepted(m.from_user.id))


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def help_cmd(m: Message):
    await m.answer(HELP)


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("لغو شد.", reply_markup=main_menu())


# ----------------------------- Submit flow ------------------------
@router.message(F.text == BTN_NEW)
async def new_news(m: Message, state: FSMContext):
    if await db.is_blocked(m.from_user.id):
        return
    if not await db.has_accepted(m.from_user.id):
        await send_rules(m)
        return
    await state.set_state(Form.waiting_text)
    await m.answer(f"متن نیوزت رو بفرست ✍️\n(حداکثر {MAX_LEN} کاراکتر)")


@router.message(Form.waiting_text, F.text)
async def got_text(m: Message, state: FSMContext):
    if len(m.text) > MAX_LEN:
        await m.answer(f"متن خیلی بلنده ({len(m.text)} کاراکتر). لطفاً کوتاه‌ترش کن.")
        return
    sent = await m.answer(
        "تگ(های) مرتبط رو انتخاب کن 👇\nاگه تگ مناسبی نبود، «➕ تگ دلخواه» رو بزن.",
        reply_markup=tags_kb([], []),
    )
    await state.update_data(text=m.text, selected=[], custom=[], kb_msg=sent.message_id)
    await state.set_state(Form.waiting_tags)


@router.message(Form.waiting_text)
async def not_text(m: Message):
    await m.answer("فعلاً فقط متن قبول می‌کنم. لطفاً نیوزت رو به صورت متن بفرست.")


TAG_STATES = StateFilter(Form.waiting_tags, Form.waiting_custom_tag)


@router.callback_query(TAG_STATES, F.data.startswith("tag:"))
async def toggle_tag(c: CallbackQuery, state: FSMContext):
    idx = int(c.data.split(":")[1])
    data = await state.get_data()
    selected: list[int] = data.get("selected", [])
    if idx in selected:
        selected.remove(idx)
    else:
        selected.append(idx)
    await state.update_data(selected=selected)
    await c.message.edit_reply_markup(reply_markup=tags_kb(selected, data.get("custom", [])))
    await c.answer()


@router.callback_query(TAG_STATES, F.data.startswith("ctag_del:"))
async def delete_custom(c: CallbackQuery, state: FSMContext):
    idx = int(c.data.split(":")[1])
    data = await state.get_data()
    custom: list[str] = data.get("custom", [])
    if 0 <= idx < len(custom):
        custom.pop(idx)
    await state.update_data(custom=custom)
    await c.message.edit_reply_markup(reply_markup=tags_kb(data.get("selected", []), custom))
    await c.answer()


@router.callback_query(TAG_STATES, F.data == "tag_custom")
async def ask_custom(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if len(data.get("custom", [])) >= MAX_CUSTOM:
        await c.answer(f"حداکثر {MAX_CUSTOM} تگ دلخواه می‌تونی اضافه کنی.", show_alert=True)
        return
    await state.set_state(Form.waiting_custom_tag)
    await c.message.answer("تگ دلخواهت رو بفرست ✍️\n(مثلاً: دانشگاه_فرهنگیان)\nبرای برگشت: /back")
    await c.answer()


@router.message(Command("back"), Form.waiting_custom_tag)
async def back_to_tags(m: Message, state: FSMContext):
    await state.set_state(Form.waiting_tags)
    await m.answer("باشه، همون منوی تگ‌ها بالاست 👆")


@router.message(Form.waiting_custom_tag, F.text)
async def got_custom(m: Message, state: FSMContext, bot: Bot):
    tag = clean_tag(m.text)
    data = await state.get_data()
    custom: list[str] = data.get("custom", [])
    if tag is None:
        await m.answer("تگ معتبر نیست. فقط حرف و عدد بفرست (بین ۲ تا ۳۰ کاراکتر).")
        return
    if tag in TAGS or tag in custom:
        await m.answer("این تگ قبلاً هست. یه تگ دیگه بفرست یا /back بزن.")
        return
    custom.append(tag)
    await state.update_data(custom=custom)
    await state.set_state(Form.waiting_tags)
    data["custom"] = custom
    await refresh_kb(bot, m.chat.id, data)
    await m.answer(f"✅ تگ {tag} اضافه شد. حالا «📨 ثبت نهایی» رو بزن.")


@router.message(Form.waiting_custom_tag)
async def custom_not_text(m: Message):
    await m.answer("لطفاً تگ رو به صورت متن بفرست.")


@router.callback_query(TAG_STATES, F.data == "tags_cancel")
async def tags_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("لغو شد.")
    await c.answer()


@router.callback_query(TAG_STATES, F.data == "tags_done")
async def tags_done(c: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    selected: list[int] = sorted(data.get("selected", []))
    custom: list[str] = data.get("custom", [])
    if not selected and not custom:
        await c.answer("حداقل یک تگ انتخاب کن یا تگ دلخواه بساز.", show_alert=True)
        return

    user = c.from_user
    if await db.is_blocked(user.id):
        await state.clear()
        return

    tags = " ".join([TAGS[i] for i in selected] + custom)
    cid = await db.add_news(user.id, user.full_name, user.username, data["text"], tags)
    await state.clear()

    admin_text = (
        f"📥 <b>نیوز جدید #{cid}</b>\n"
        f"👤 نام: {html.escape(user.full_name)}\n"
        f"🔗 یوزرنیم: {'@' + user.username if user.username else 'ندارد'}\n"
        f"🆔 آیدی: <code>{user.id}</code>\n\n"
        f"{html.escape(data['text'])}\n\n"
        f"🏷 {html.escape(tags)}"
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
    # Atomically claim the item so two admins can't handle it twice
    row = await db.claim(cid)
    if row is None:
        await c.answer("این نیوز قبلاً بررسی شده یا پیدا نشد.", show_alert=True)
        return

    admin_name = html.escape(c.from_user.full_name)

    if action == "ok":
        post = (
            f"{html.escape(row['text'])}\n\n"
            f"{html.escape(row['tags'])}\n\n"
            f"{POST_FOOTER.format(id=cid, channel=html.escape(CHANNEL_USERNAME))}"
        )
        try:
            sent = await bot.send_message(CHANNEL_ID, post)
        except Exception as e:
            await db.set_status(cid, "pending")
            await c.answer(f"خطا در ارسال به کانال: {e}", show_alert=True)
            return
        await db.set_status(cid, "approved", sent.message_id)
        note = "🎉 نیوزت تایید شد و تو کانال منتشر شد."
        if CHANNEL_USERNAME.startswith("@"):
            note += f"\nhttps://t.me/{CHANNEL_USERNAME[1:]}/{sent.message_id}"
        await _safe_send(bot, row["user_id"], note)
        verdict = f"✅ تایید شد توسط {admin_name}"

    elif action == "no":
        await db.set_status(cid, "rejected")
        await _safe_send(bot, row["user_id"], "متاسفانه نیوز شما تایید نشد.")
        verdict = f"❌ رد شد توسط {admin_name}"

    else:  # ban
        await db.set_status(cid, "rejected")
        await db.block(row["user_id"])
        verdict = f"🚫 رد و کاربر بلاک شد توسط {admin_name}"

    await c.message.edit_text(c.message.html_text + f"\n\n{verdict}", reply_markup=None)
    await c.answer()


async def _safe_send(bot: Bot, user_id: int, text: str):
    try:
        await bot.send_message(user_id, text)
    except Exception as e:
        logging.warning("Could not message user %s: %s", user_id, e)


# Stale buttons (e.g. after a restart cleared the in-memory state)
@router.callback_query(F.data.regexp(r"^(tag|ctag)"))
async def stale_buttons(c: CallbackQuery):
    await c.answer("این منو منقضی شده. دوباره «✍️ ثبت نیوز» رو بزن.", show_alert=True)


# ----------------------------- Main -------------------------------
async def start_health_server():
    """Tiny web server so hosts like Render (and UptimeRobot pings) have a URL to hit."""
    port = int(os.getenv("PORT", "0"))
    if not port:
        return

    async def ok(_request):
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logging.info("Health server listening on port %s", port)


async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN or not CHANNEL_ID or not ADMIN_IDS:
        raise SystemExit("Set BOT_TOKEN, CHANNEL_ID and ADMIN_IDS environment variables.")
    await start_health_server()
    await db.connect(DATABASE_URL, DB_PATH)
    logging.info("Storage backend: %s", db.backend)
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="شروع و منوی اصلی"),
            BotCommand(command="help", description="راهنما"),
            BotCommand(command="rules", description="قوانین"),
            BotCommand(command="cancel", description="لغو ارسال نیوز"),
        ]
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
