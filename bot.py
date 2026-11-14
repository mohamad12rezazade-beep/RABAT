
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
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)

CHANNEL = "channel"
POST_TEXT = "post_text"
BUTTON_URL = "button_url"

user_steps = {}


# ---------------- DATABASE ----------------

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
                    button_url TEXT,
                    message_id BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

    logger.info("Database initialized")


def save_user(user):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (
                    telegram_id,
                    username,
                    first_name
                )
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


def save_creation(uid, channel_id, title, post, url, message_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO creations (
                    telegram_id,
                    channel_id,
                    channel_title,
                    post_text,
                    button_url,
                    message_id
                )
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                uid,
                str(channel_id),
                title,
                post,
                url,
                message_id
            ))


def get_user_count():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM users")
            return cur.fetchone()["total"]


# ---------------- KEYBOARDS ----------------

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔘 ساخت دکمه شیشه‌ای",
                callback_data="create"
            )
        ],
        [
            InlineKeyboardButton(
                "👤 کاربر",
                callback_data="user"
            )
        ]
    ])


def back_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "↩️ بازگشت به منو",
                callback_data="home"
            )
        ]
    ])


# ---------------- START ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_steps.pop(user.id, None)

    try:
        await asyncio.to_thread(save_user, user)
    except Exception:
        logger.exception("Could not save user")

    await update.message.reply_text(
        "سلام 👋\n\n"
        "به ربات ساخت دکمه شیشه‌ای خوش اومدی.\n"
        "از منوی زیر استفاده کن:",
        reply_markup=main_keyboard()
    )


# ---------------- CALLBACKS ----------------

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
        user_steps[uid] = {
            "step": CHANNEL
        }

        await query.message.reply_text(
            "🔗 لینک کانالی که می‌خوای پست داخلش ارسال بشه رو بفرست.\n\n"
            "مثال:\n"
            "@MyChannel\n"
            "یا لینک کانال عمومی\n\n"
            "حتماً خودت و بات باید در کانال ادمین باشید.",
            reply_markup=back_keyboard()
        )

    elif query.data == "user":
        try:
            count = await asyncio.to_thread(get_user_count)
        except Exception:
            logger.exception("User count error")
            count = "نامشخص"

        await query.message.reply_text(
            f"👤 اطلاعات شما\n\n"
            f"آیدی: {uid}\n"
            f"نام: {user.first_name}\n"
            f"یوزرنیم: @{user.username or 'ندارد'}\n\n"
            f"👥 تعداد کاربران ثبت‌شده: {count}",
            reply_markup=main_keyboard()
        )


# ---------------- CHANNEL LINK ----------------

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


# ---------------- MESSAGE HANDLER ----------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id

    if uid not in user_steps:
        return

    state = user_steps[uid]
    step = state["step"]
    text = update.message.text.strip()

    if step == CHANNEL:

        channel = parse_channel(text)

        if not channel:
            await update.message.reply_text(
                "❌ لینک کانال صحیح نیست.\n"
                "لطفاً آیدی عمومی کانال را بفرست."
            )
            return

        try:
            chat = await context.bot.get_chat(channel)

            creator = await context.bot.get_chat_member(
                chat.id,
                uid
            )

            if creator.status not in ("administrator", "creator"):
                await update.message.reply_text(
                    "❌ شما در این کانال ادمین نیستید."
                )
                return

            bot_member = await context.bot.get_chat_member(
                chat.id,
                context.bot.id
            )

            if bot_member.status not in ("administrator", "creator"):
                await update.message.reply_text(
                    "❌ ابتدا بات را در کانال ادمین کنید."
                )
                return

            if (
                bot_member.status == "administrator"
                and not bot_member.can_post_messages
            ):
                await update.message.reply_text(
                    "❌ بات اجازه ارسال پست در کانال را ندارد."
                )
                return

            state["channel_id"] = chat.id
            state["channel_title"] = chat.title or channel
            state["step"] = POST_TEXT

            await update.message.reply_text(
                "✅ کانال تأیید شد.\n\n"
                "📝 حالا متن پست را بفرست."
            )

        except Exception:
            logger.exception("Channel validation failed")

            await update.message.reply_text(
                "❌ بررسی کانال انجام نشد.\n"
                "آیدی کانال و دسترسی ادمین بات را بررسی کن."
            )

    elif step == POST_TEXT:

        if not text:
            await update.message.reply_text("متن پست خالی است.")
            return

        state["post_text"] = text
        state["step"] = BUTTON_URL

        await update.message.reply_text(
            "🔗 حالا لینک مقصد دکمه شیشه‌ای را بفرست.\n\n"
            "مثال:\n"
            "https://example.com"
        )

    elif step == BUTTON_URL:

        if not re.fullmatch(r"https?://\S+", text, re.I):
            await update.message.reply_text(
                "❌ لینک معتبر نیست.\n"
                "لطفاً لینک را با https:// بفرست."
            )
            return

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔗 ورود به لینک",
                    url=text
                )
            ]
        ])

        try:
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
                    text,
                    sent.message_id
                )
            except Exception:
                logger.exception("Could not save creation")

            await update.message.reply_text(
                "✅ پست با موفقیت ارسال شد!\n\n"
                "🔘 دکمه شیشه‌ای ساخته شد.\n"
                "📤 می‌تونی پیام رو فوروارد کنی؛ "
                "دکمه هم همراه پیام باقی می‌مونه.",
                reply_markup=main_keyboard()
            )

            user_steps.pop(uid, None)

        except Exception:
            logger.exception("Post sending failed")

            await update.message.reply_text(
                "❌ ارسال پیام ناموفق بود.\n"
                "دسترسی بات به ارسال پیام در کانال را بررسی کن."
            )


# ---------------- CANCEL ----------------

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_steps.pop(update.effective_user.id, None)

    await update.message.reply_text(
        "❌ عملیات لغو شد.",
        reply_markup=main_keyboard()
    )


# ---------------- MAIN ----------------

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(CallbackQueryHandler(buttons))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    logger.info("Bot is running!")

    app.run_polling()


if __name__ == "__main__":
    main()
