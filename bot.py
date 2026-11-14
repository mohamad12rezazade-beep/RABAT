
import os
import re
import logging

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
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Environment variables are missing!")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# مراحل ساخت
CHANNEL = "channel"
POST_TEXT = "post_text"
BUTTON_URL = "button_url"

# ذخیره مرحله کاربران
user_steps = {}


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "ساخت دکمه شیشه‌ای",
            callback_data="create"
        )],
        [InlineKeyboardButton(
            "کاربر",
            callback_data="user"
        )]
    ])


def save_user(user):
    try:
        supabase.table("users").upsert({
            "telegram_id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or ""
        }, on_conflict="telegram_id").execute()
    except Exception as e:
        logger.error("Save user error: %s", e)


def get_channel_username(text):
    text = text.strip()

    if text.startswith("@"):
        return text

    match = re.search(
        r"(?:https?://)?t\.me/([a-zA-Z0-9_]{5,})/?$",
        text
    )

    if match:
        return "@" + match.group(1)

    return None


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    save_user(user)

    user_steps.pop(user.id, None)

    await update.message.reply_text(
        "سلام 👋\n"
        "به ربات ساخت دکمه شیشه‌ای خوش اومدی.\n\n"
        "یکی از گزینه‌های زیر رو انتخاب کن:",
        reply_markup=main_keyboard()
    )


async def buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    user = query.from_user

    if query.data == "create":
        user_steps[user.id] = {
            "step": CHANNEL
        }

        await query.message.reply_text(
            "🔗 لینک کانالی که می‌خوای پست داخلش ارسال بشه رو بفرست.\n\n"
            "مثال:\n"
            "@MyChannel\n"
            "یا\n"
            "https://t.me/MyChannel\n\n"
            "نکته: بات و خودت باید در کانال ادمین باشید."
        )

    elif query.data == "user":
        try:
            result = supabase.table("users").select(
                "telegram_id"
            ).execute()

            count = len(result.data or [])
        except Exception:
            count = 0

        await query.message.reply_text(
            f"👤 اطلاعات کاربری\n\n"
            f"آیدی شما: `{user.id}`\n"
            f"نام: {user.first_name}\n"
            f"یوزرنیم: @{user.username or 'ندارد'}\n\n"
            f"تعداد کاربران ثبت‌شده: {count}",
            parse_mode="Markdown"
        )


async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    uid = user.id

    if uid not in user_steps:
        return

    current = user_steps[uid]
    step = current["step"]
    text = update.message.text.strip()

    # مرحله اول: دریافت کانال
    if step == CHANNEL:

        channel = get_channel_username(text)

        if not channel:
            await update.message.reply_text(
                "❌ لینک کانال معتبر نیست.\n"
                "لطفاً لینک عمومی کانال یا آیدی آن را بفرست."
            )
            return

        try:
            chat = await context.bot.get_chat(channel)

            # بررسی ادمین بودن کاربر
            member = await context.bot.get_chat_member(
                chat.id,
                uid
            )

            if member.status not in ("administrator", "creator"):
                await update.message.reply_text(
                    "❌ شما باید در این کانال ادمین باشید."
                )
                return

            # بررسی ادمین بودن بات
            bot_member = await context.bot.get_chat_member(
                chat.id,
                context.bot.id
            )

            if bot_member.status not in ("administrator", "creator"):
                await update.message.reply_text(
                    "❌ ابتدا بات را در کانال ادمین کنید."
                )
                return

            current["channel_id"] = chat.id
            current["channel_title"] = chat.title or channel
            current["step"] = POST_TEXT

            await update.message.reply_text(
                "✅ کانال تأیید شد.\n\n"
                "📝 حالا متن پستی که می‌خوای داخل کانال ارسال بشه رو بفرست."
            )

        except Exception as e:
            logger.error("Channel check error: %s", e)

            await update.message.reply_text(
                "❌ نتونستم کانال رو بررسی کنم.\n"
                "مطمئن شو کانال عمومی است و بات داخل آن ادمین است."
            )

    # مرحله دوم: دریافت متن
    elif step == POST_TEXT:

        current["post_text"] = text
        current["step"] = BUTTON_URL

        await update.message.reply_text(
            "🔗 حالا لینک مقصد دکمه شیشه‌ای رو بفرست.\n\n"
            "مثال:\n"
            "https://example.com"
        )

    # مرحله سوم: دریافت لینک و ارسال پست
    elif step == BUTTON_URL:

        if not re.match(r"^https?://\S+$", text, re.I):
            await update.message.reply_text(
                "❌ لینک معتبر نیست.\n"
                "لطفاً لینکی با https:// یا http:// بفرست."
            )
            return

        current["button_url"] = text

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
                chat_id=current["channel_id"],
                text=current["post_text"],
                reply_markup=keyboard
            )

            # ذخیره در Supabase
            try:
                supabase.table("creations").insert({
                    "telegram_id": uid,
                    "channel_id": str(current["channel_id"]),
                    "channel_title": current["channel_title"],
                    "post_text": current["post_text"],
                    "button_url": text,
                    "message_id": sent.message_id
                }).execute()
            except Exception as e:
                logger.error("Database save error: %s", e)

            await update.message.reply_text(
                "✅ پست با موفقیت داخل کانال ارسال شد!\n\n"
                "🔘 دکمه شیشه‌ای ساخته شد.\n"
                "📤 با فوروارد کردن پیام، دکمه هم همراهش می‌مونه.",
                reply_markup=main_keyboard()
            )

        except Exception as e:
            logger.error("Send post error: %s", e)

            await update.message.reply_text(
                "❌ ارسال پست انجام نشد.\n"
                "دسترسی ارسال پیام بات در کانال رو بررسی کن."
            )

        finally:
            user_steps.pop(uid, None)


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_steps.pop(update.effective_user.id, None)

    await update.message.reply_text(
        "عملیات لغو شد.",
        reply_markup=main_keyboard()
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(
        CallbackQueryHandler(buttons)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    logger.info("Bot started!")

    app.run_polling()


if __name__ == "__main__":
    main()
