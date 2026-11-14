
import os
import re
import logging
import asyncio

import psycopg2
from psycopg2.extras import RealDictCursor

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
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
BUTTON_TEXT = "button_text"

PAGE_SIZE = 5
user_steps = {}


# ================= DATABASE =================

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
                    button_text TEXT,
                    message_id BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # سازگاری با نسخه‌های قبلی دیتابیس
            cur.execute("""
                ALTER TABLE creations
                ADD COLUMN IF NOT EXISTS photo_file_id TEXT
            """)

            cur.execute("""
                ALTER TABLE creations
                ADD COLUMN IF NOT EXISTS button_text TEXT
            """)


def save_user(user):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (
                    telegram_id, username, first_name
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


def save_creation(
    uid,
    channel_id,
    title,
    post,
    photo,
    url,
    button_text,
    message_id
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO creations (
                    telegram_id,
                    channel_id,
                    channel_title,
                    post_text,
                    photo_file_id,
                    button_url,
                    button_text,
                    message_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                uid,
                str(channel_id),
                title,
                post,
                photo,
                url,
                button_text,
                message_id
            ))

            return cur.fetchone()["id"]


def get_user_count():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM users")
            return cur.fetchone()["total"]


def get_user_posts(uid, page=0):
    with get_db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT *
                FROM creations
                WHERE telegram_id = %s
                ORDER BY id DESC
                LIMIT %s OFFSET %s
            """, (
                uid,
                PAGE_SIZE,
                page * PAGE_SIZE
            ))

            posts = cur.fetchall()

            cur.execute("""
                SELECT COUNT(*) AS total
                FROM creations
                WHERE telegram_id = %s
            """, (uid,))

            total = cur.fetchone()["total"]

            return posts, total


def get_post(uid, post_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM creations
                WHERE id = %s AND telegram_id = %s
            """, (post_id, uid))

            return cur.fetchone()


def delete_post_record(uid, post_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM creations
                WHERE id = %s AND telegram_id = %s
                RETURNING id
            """, (post_id, uid))

            return cur.fetchone() is not None


# ================= KEYBOARDS =================

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
                "🗑 حذف پست",
                callback_data="delete_list"
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


# ================= START =================

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


# ================= CANCEL =================

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_steps.pop(uid, None)

    await update.message.reply_text(
        "❌ عملیات لغو شد.",
        reply_markup=main_keyboard()
    )


# ================= POSTS LIST =================

async def show_posts(query, uid, page=0):
    posts, total = await asyncio.to_thread(
        get_user_posts,
        uid,
        page
    )

    if not posts:
        await query.message.reply_text(
            "📭 هنوز پستی نساختی.",
            reply_markup=main_keyboard()
        )
        return

    keyboard = []

    for post in posts:
        title = post["channel_title"] or "کانال"

        keyboard.append([
            InlineKeyboardButton(
                f"🗑 پست {post['id']} | {title[:25]}",
                callback_data=f"delask:{post['id']}"
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ قبلی",
                callback_data=f"list:{page - 1}"
            )
        )

    if (page + 1) * PAGE_SIZE < total:
        navigation.append(
            InlineKeyboardButton(
                "بعدی ➡️",
                callback_data=f"list:{page + 1}"
            )
        )

    if navigation:
        keyboard.append(navigation)

    keyboard.append([
        InlineKeyboardButton(
            "🏠 منوی اصلی",
            callback_data="home"
        )
    ])

    await query.message.reply_text(
        f"🗑 مدیریت پست‌های شما\n\n"
        f"تعداد کل پست‌ها: {total}\n"
        f"صفحه: {page + 1}\n\n"
        f"برای حذف یک پست، دکمه مربوط به آن را بزن:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ================= CALLBACKS =================

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    data = query.data

    if data == "home":
        user_steps.pop(uid, None)

        await query.message.reply_text(
            "🏠 منوی اصلی:",
            reply_markup=main_keyboard()
        )

    elif data == "create":
        user_steps[uid] = {"step": CHANNEL}

        await query.message.reply_text(
            "🔗 لینک عمومی کانال رو بفرست.\n\n"
            "مثال: @MyChannel\n\n"
            "شما و بات باید در کانال ادمین باشید.",
            reply_markup=back_keyboard()
        )

    elif data == "user":
        try:
            count = await asyncio.to_thread(get_user_count)
        except Exception:
            logger.exception("User count failed")
            count = "نامشخص"

        user = query.from_user

        await query.message.reply_text(
            f"👤 اطلاعات شما\n\n"
            f"آیدی: {uid}\n"
            f"نام: {user.first_name}\n"
            f"یوزرنیم: @{user.username or 'ندارد'}\n\n"
            f"👥 تعداد کاربران: {count}",
            reply_markup=main_keyboard()
        )

    elif data == "delete_list":
        await show_posts(query, uid)

    elif data.startswith("list:"):
        try:
            page = int(data.split(":")[1])
            if page < 0:
                return
            await show_posts(query, uid, page)
        except (ValueError, IndexError):
            await query.message.reply_text("❌ صفحه نامعتبر است.")

    elif data.startswith("delask:"):
        try:
            post_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            return

        post = await asyncio.to_thread(
            get_post,
            uid,
            post_id
        )

        if not post:
            await query.message.reply_text(
                "❌ این پست پیدا نشد یا متعلق به شما نیست."
            )
            return

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ بله، حذف کن",
                    callback_data=f"delconfirm:{post_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ انصراف",
                    callback_data="delete_list"
                )
            ]
        ])

        await query.message.reply_text(
            f"⚠️ تأیید حذف پست\n\n"
            f"کانال: {post['channel_title'] or 'نامشخص'}\n"
            f"شناسه پست: {post_id}\n\n"
            f"آیا می‌خوای این پست از کانال حذف بشه؟",
            reply_markup=keyboard
        )

    elif data.startswith("delconfirm:"):
        try:
            post_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            return

        post = await asyncio.to_thread(
            get_post,
            uid,
            post_id
        )

        if not post:
            await query.message.reply_text(
                "❌ پست پیدا نشد یا قبلاً حذف شده است."
            )
            return

        try:
            channel_id = int(post["channel_id"])

            bot_member = await context.bot.get_chat_member(
                channel_id,
                context.bot.id
            )

            if bot_member.status != "creator":
                if (
                    bot_member.status != "administrator"
                    or not getattr(
                        bot_member,
                        "can_delete_messages",
                        False
                    )
                ):
                    await query.message.reply_text(
                        "❌ بات اجازه حذف پیام در این کانال رو نداره.\n"
                        "دسترسی حذف پیام رو به بات بده."
                    )
                    return

            await context.bot.delete_message(
                chat_id=channel_id,
                message_id=post["message_id"]
            )

            await asyncio.to_thread(
                delete_post_record,
                uid,
                post_id
            )

            await query.message.reply_text(
                "✅ پست با موفقیت از کانال حذف شد.",
                reply_markup=main_keyboard()
            )

        except Exception:
            logger.exception("Delete message failed")

            await query.message.reply_text(
                "❌ حذف پست انجام نشد.\n\n"
                "موارد زیر رو بررسی کن:\n"
                "۱. بات هنوز ادمین کانال باشه.\n"
                "۲. دسترسی حذف پیام داشته باشه.\n"
                "۳. پیام قبلاً حذف نشده باشه."
            )


# ================= CHANNEL PARSER =================

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


# ================= MESSAGE HANDLER =================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    uid = user.id

    if uid not in user_steps:
        return

    state = user_steps[uid]
    step = state["step"]
    message = update.message

    text = message.text.strip() if message.text else ""

    # -------- STEP 1: CHANNEL --------

    if step == CHANNEL:

        channel = parse_channel(text)

        if not channel:
            await message.reply_text(
                "❌ لینک کانال معتبر نیست.\n"
                "مثال: @MyChannel"
            )
            return

        try:
            chat = await context.bot.get_chat(channel)

            member = await context.bot.get_chat_member(
                chat.id,
                uid
            )

            if member.status not in ("administrator", "creator"):
                await message.reply_text(
                    "❌ شما باید ادمین کانال باشید."
                )
                return

            bot_member = await context.bot.get_chat_member(
                chat.id,
                context.bot.id
            )

            if bot_member.status not in ("administrator", "creator"):
                await message.reply_text(
                    "❌ بات باید ادمین کانال باشد."
                )
                return

            if bot_member.status == "administrator":
                if not getattr(bot_member, "can_post_messages", False):
                    await message.reply_text(
                        "❌ بات اجازه ارسال پست ندارد."
                    )
                    return

            state["channel_id"] = chat.id
            state["channel_title"] = chat.title or channel
            state["step"] = POST

            await message.reply_text(
                "✅ کانال تأیید شد.\n\n"
                "📸 حالا عکس همراه کپشن یا فقط متن پست رو بفرست."
            )

        except Exception:
            logger.exception("Channel validation failed")

            await message.reply_text(
                "❌ بررسی کانال ناموفق بود.\n"
                "آیدی کانال و دسترسی بات رو بررسی کن."
            )

    # -------- STEP 2: POST --------

    elif step == POST:

        if message.photo:
            state["photo_id"] = message.photo[-1].file_id
            state["post_text"] = message.caption or ""

        elif message.text:
            state["photo_id"] = None
            state["post_text"] = message.text.strip()

            if not state["post_text"]:
                await message.reply_text(
                    "❌ متن پست نمی‌تونه خالی باشه."
                )
                return

        else:
            await message.reply_text(
                "❌ لطفاً عکس یا متن ارسال کن."
            )
            return

        state["step"] = BUTTON_URL

        await message.reply_text(
            "✅ محتوای پست دریافت شد.\n\n"
            "🔗 حالا لینک مقصد دکمه رو بفرست.\n"
            "مثال: https://example.com"
        )

    # -------- STEP 3: BUTTON URL --------

    elif step == BUTTON_URL:

        if not re.fullmatch(r"https?://\S+", text, re.I):
            await message.reply_text(
                "❌ لینک معتبر نیست.\n"
                "لطفاً لینک رو با https:// یا http:// بفرست."
            )
            return

        state["button_url"] = text
        state["step"] = BUTTON_TEXT

        await message.reply_text(
            "✏️ مرحله چهارم:\n\n"
            "اسم دلخواه دکمه شیشه‌ای رو وارد کن.\n\n"
            "مثال:\n"
            "🔗 ورود به سایت\n"
            "📢 عضویت در کانال\n"
            "🎁 دریافت هدیه"
        )

    # -------- STEP 4: BUTTON TEXT & SEND --------

    elif step == BUTTON_TEXT:

        button_name = text.strip()

        if not button_name:
            await message.reply_text(
                "❌ اسم دکمه نمی‌تونه خالی باشه."
            )
            return

        if len(button_name) > 64:
            await message.reply_text(
                "❌ اسم دکمه نباید بیشتر از ۶۴ کاراکتر باشه."
            )
            return

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    button_name,
                    url=state["button_url"]
                )
            ]
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
                    state["button_url"],
                    button_name,
                    sent.message_id
                )

            except Exception:
                logger.exception("Saving creation failed")

                user_steps.pop(uid, None)

                await message.reply_text(
                    "⚠️ پست در کانال منتشر شد، اما ذخیره اطلاعات آن در دیتابیس ناموفق بود.\n"
                    "بنابراین ممکن است در فهرست حذف پست نمایش داده نشود.",
                    reply_markup=main_keyboard()
                )
                return

            user_steps.pop(uid, None)

            await message.reply_text(
                "✅ پست با موفقیت در کانال منتشر شد.\n\n"
                f"🔘 اسم دکمه: {button_name}\n"
                f"🔗 لینک: {state['button_url']}",
                reply_markup=main_keyboard()
            )

        except Exception:
            logger.exception("Post sending failed")

            await message.reply_text(
                "❌ ارسال پست ناموفق بود.\n"
                "دسترسی ارسال پست و محتوای ارسالی رو بررسی کن.\n\n"
                "برای تلاش دوباره، دوباره اسم دکمه رو بفرست."
            )


# ================= ERROR HANDLER =================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    logger.error(
        "Unhandled exception:",
        exc_info=context.error
    )


# ================= MAIN =================

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(
        CallbackQueryHandler(buttons)
    )

    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO) & ~filters.COMMAND,
            handle_message
        )
    )

    app.add_error_handler(error_handler)

    logger.info("Bot started")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
