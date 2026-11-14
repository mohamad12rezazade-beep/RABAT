
import os
import re
import logging
import asyncio

import psycopg2
from psycopg2.extras import RealDictCursor

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN or not DATABASE_URL:
    raise RuntimeError("BOT_TOKEN یا DATABASE_URL تنظیم نشده است")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

CHANNEL = "channel"
POST = "post"
BUTTON_URL = "button_url"

user_steps = {}


# =============== DATABASE ===============

def get_db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor,
        connect_timeout=15
    )


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id BIGINT PRIMARY KEY,
                    username TEXT DEFAULT '',
                    first_name TEXT DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS creations (
                    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    telegram_id BIGINT NOT NULL,
                    channel_id TEXT NOT NULL,
                    channel_title TEXT,
                    post_text TEXT,
                    photo_file_id TEXT,
                    button_url TEXT,
                    message_id BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)


def save_user(user):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users
                (telegram_id, username, first_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name
            """, (
                user.id,
                user.username or "",
                user.first_name or ""
            ))


def save_creation(uid, channel_id, title, post, photo, url, message_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO creations (
                    telegram_id, channel_id, channel_title,
                    post_text, photo_file_id, button_url, message_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                uid, str(channel_id), title,
                post, photo, url, message_id
            ))


def get_user_count():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM users")
            return cur.fetchone()["total"]


# =============== KEYBOARDS ===============

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🔘 ساخت دکمه شیشه‌ای",
            callback_data="create"
        )],
        [InlineKeyboardButton(
            "👤 کاربر",
            callback_data="user"
        )]
    ])


def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "↩️ بازگشت به منو",
            callback_data="home"
        )]
    ])


# =============== START ===============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_steps.pop(user.id, None)

    try:
        await asyncio.to_thread(save_user, user)
    except Exception:
        logger.exception("Save user failed")

    await update.message.reply_text(
        "سلام 👋\n\n"
        "به ربات ساخت دکمه شیشه‌ای خوش اومدی.\n"
        "یکی از گزینه‌های زیر رو انتخاب کن:",
        reply_markup=main_keyboard()
    )


# =============== CALLBACKS ===============

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    uid = user.id

    if query.data == "home":
        user_steps.pop(uid, None)

        await query.message.reply_text(
            "🏠 منوی اصلی:",
            reply_markup=main_keyboard()
        )

    elif query.data == "create":
        user_steps[uid] = {"step": CHANNEL}

        await query.message.reply_text(
            "🔗 لینک کانالی که می‌خوای پست داخلش ارسال بشه رو بفرست.\n\n"
            "مثال: @MyChannel\n\n"
            "حتماً خودت و بات باید در کانال ادمین باشید.",
            reply_markup=back_keyboard()
        )

    elif query.data == "user":
        try:
            count = await asyncio.to_thread(get_user_count)
        except Exception:
            logger.exception("Count error")
            count = "نامشخص"

        await query.message.reply_text(
            f"👤 اطلاعات شما\n\n"
            f"آیدی: {uid}\n"
            f"نام: {user.first_name}\n"
            f"یوزرنیم: @{user.username or 'ندارد'}\n\n"
            f"👥 تعداد کاربران: {count}",
            reply_markup=main_keyboard()
        )


# =============== CHANNEL PARSER ===============

def parse_channel(value):
    value = value.strip()

    if re.fullmatch(r"@[A-Za-z0-9_]{5,}", value):
        return value

    match = re.fullmatch(
        r"https?://t\.me/([A-Za-z0-9_]{5,})/?",
        value
    )

    if match:
        return "@" + match.group(1)

    return None


# =============== MESSAGE HANDLER ===============

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id

    if uid not in user_steps:
        return

    state = user_steps[uid]
    step = state["step"]
    message = update.message

    # STEP 1: CHANNEL

    if step == CHANNEL:
        text = message.text.strip() if message.text else ""
        channel = parse_channel(text)

        if not channel:
            await message.reply_text(
                "❌ لینک کانال معتبر نیست.\n"
                "لطفاً آیدی عمومی کانال را بفرست."
            )
            return

        try:
            chat = await context.bot.get_chat(channel)

            member = await context.bot.get_chat_member(
                chat.id, uid
            )

            if member.status not in ("administrator", "creator"):
                await message.reply_text(
                    "❌ شما باید ادمین کانال باشید."
                )
                return

            bot_member = await context.bot.get_chat_member(
                chat.id, context.bot.id
            )

            if bot_member.status not in ("administrator", "creator"):
                await message.reply_text(
                    "❌ بات باید ادمین کانال باشد."
                )
                return

            if (
                bot_member.status == "administrator"
                and not bot_member.can_post_messages
            ):
                await message.reply_text(
                    "❌ بات اجازه ارسال پست ندارد."
                )
                return

            state["channel_id"] = chat.id
            state["channel_title"] = chat.title or channel
            state["step"] = POST

            await message.reply_text(
                "✅ کانال تأیید شد.\n\n"
                "📸 حالا عکس همراه متن پست یا فقط متن پست رو بفرست."
            )

        except Exception:
            logger.exception("Channel validation failed")

            await message.reply_text(
                "❌ بررسی کانال ناموفق بود.\n"
                "آیدی کانال و دسترسی بات را بررسی کن."
            )

    # STEP 2: PHOTO OR TEXT

    elif step == POST:

        if message.photo:
            state["photo_id"] = message.photo[-1].file_id
            state["post_text"] = message.caption or ""

        elif message.text:
            state["photo_id"] = None
            state["post_text"] = message.text.strip()

        else:
            await message.reply_text(
                "❌ لطفاً عکس یا متن ارسال کن."
            )
            return

        state["step"] = BUTTON_URL

        await message.reply_text(
            "🔗 حالا لینک مقصد دکمه شیشه‌ای رو بفرست.\n\n"
            "مثال:\n"
            "https://example.com"
        )

    # STEP 3: BUTTON URL AND SEND

    elif step == BUTTON_URL:

        text = message.text.strip() if message.text else ""

        if not re.fullmatch(r"https?://\S+", text, re.I):
            await message.reply_text(
                "❌ لینک معتبر نیست.\n"
                "لطفاً لینک را با https:// بفرست."
            )
            return

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🔗 ورود به لینک",
                url=text
            )]
        ])

        try:
            if state.get("photo_id"):
                sent = await context.bot.send_photo(
                    chat_id=state["channel_id"],
                    photo=state["photo_id"],
                    caption=state["post_text"] or None,
                    reply_markup=keyboard
                )

            else:
                if not state["post_text"]:
                    await message.reply_text(
                        "❌ متن پست خالی است."
                    )
                    return

                sent = await context.bot.send_message(
                    chat_id=state["channel_id"],
                    text=state["post_text"],
                    reply_markup=keyboard
                )

            try:
                await asyncio.to_thread(
                    save_creation,
                    uid,
                    state["channel_id"],
                    state["channel_title"],
                    state["post_text"],
                    state.get("photo_id"),
                    text,
                    sent.message_id
                )
            except Exception:
                logger.exception("Save creation failed")

            user_steps.pop(uid, None)

            await message.reply_text(
                "✅ پست با موفقیت ارسال شد!\n\n"
                "📸 عکس و متن ارسال شدند.\n"
                "🔘 دکمه شیشه‌ای ساخته شد.\n"
                "📤 با فوروارد معمولی پیام، دکمه هم همراهش می‌ماند.",
                reply_markup=main_keyboard()
            )

        except Exception:
            logger.exception("Sending post failed")

            await message.reply_text(
                "❌ ارسال پست ناموفق بود.\n"
                "دسترسی بات در کانال را بررسی کن."
            )


# =============== CANCEL ===============

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_steps.pop(update.effective_user.id, None)

    await update.message.reply_text(
        "❌ عملیات لغو شد.",
        reply_markup=main_keyboard()
    )


# =============== MAIN ===============

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(CallbackQueryHandler(buttons))

    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO) & ~filters.COMMAND,
            handle_message
        )
    )

    logger.info("Bot started successfully")

    app.run_polling()


if __name__ == "__main__":
    main()
