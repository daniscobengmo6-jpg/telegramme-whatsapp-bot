import os
import glob
import asyncio
import logging
import sqlite3
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_ID = 7777462320

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "bot_data.db")
PHOTO_PATH = os.path.join(BASE_DIR, "banner.jpg")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id   INTEGER PRIMARY KEY,
                username  TEXT,
                joined_at TEXT DEFAULT (datetime('now'))
            )
        """)
        defaults = {
            "welcome_text": (
                "🎁 Bienvenue !\n\n"
                "⚽ Tu veux recevoir les scores exacts, les coupons VIP et les analyses avant tout le monde ?\n\n"
                "👇 Appuie sur le bouton ci-dessous pour rejoindre gratuitement notre chaîne WhatsApp."
            ),
            "whatsapp_url": "https://whatsapp.com/channel/0029VbC1Xd4C6ZvgosLcF531",
        }
        for k, v in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
            )
        conn.commit()


def get_setting(key: str) -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""


def set_setting(key: str, value: str):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        conn.commit()


def register_user(user_id: int, username: str | None):
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        conn.commit()


def get_all_user_ids() -> list[int]:
    with get_db() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
        return [r["user_id"] for r in rows]


def get_user_count() -> int:
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
        return row["cnt"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHECKING_MESSAGE = "🔄 Vérification de votre accès...\n⏳ Veuillez patienter..."


def find_banner() -> str | None:
    for ext in ("jpg", "jpeg", "png", "webp", "gif"):
        matches = glob.glob(os.path.join(BASE_DIR, f"banner.{ext}"))
        if matches:
            return matches[0]
    return None


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Accès refusé.")
            return
        return await func(update, context)
    return wrapper

# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    register_user(user.id, user.username)

    await update.message.reply_text(CHECKING_MESSAGE)
    await asyncio.sleep(2)

    welcome_text = get_setting("welcome_text")
    whatsapp_url = get_setting("whatsapp_url")

    button = InlineKeyboardMarkup([
        [InlineKeyboardButton("📲 Rejoindre la chaîne WhatsApp", url=whatsapp_url)]
    ])

    banner = find_banner()
    if banner:
        with open(banner, "rb") as img:
            await update.message.reply_photo(
                photo=img,
                caption=welcome_text,
                reply_markup=button,
            )
    else:
        await update.message.reply_text(welcome_text, reply_markup=button)

# ---------------------------------------------------------------------------
# /stats
# ---------------------------------------------------------------------------

@admin_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    count = get_user_count()
    await update.message.reply_text(f"📊 Utilisateurs total : *{count}*", parse_mode="Markdown")

# ---------------------------------------------------------------------------
# /settext  (ConversationHandler)
# ---------------------------------------------------------------------------

SETTEXT_WAITING = 1

@admin_only
async def settext_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    current = get_setting("welcome_text")
    await update.message.reply_text(
        f"✏️ Message actuel :\n\n{current}\n\n"
        "Envoie le nouveau message de bienvenue :"
    )
    return SETTEXT_WAITING


async def settext_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_text = update.message.text
    set_setting("welcome_text", new_text)
    await update.message.reply_text("✅ Message de bienvenue mis à jour !")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Annulé.")
    return ConversationHandler.END

# ---------------------------------------------------------------------------
# /setlink  (ConversationHandler)
# ---------------------------------------------------------------------------

SETLINK_WAITING = 1

@admin_only
async def setlink_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    current = get_setting("whatsapp_url")
    await update.message.reply_text(
        f"🔗 Lien actuel :\n{current}\n\nEnvoie le nouveau lien WhatsApp :"
    )
    return SETLINK_WAITING


async def setlink_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_url = update.message.text.strip()
    if not new_url.startswith("http"):
        await update.message.reply_text("⚠️ Le lien doit commencer par http:// ou https://")
        return SETLINK_WAITING
    set_setting("whatsapp_url", new_url)
    await update.message.reply_text("✅ Lien WhatsApp mis à jour !")
    return ConversationHandler.END

# ---------------------------------------------------------------------------
# /setphoto  (ConversationHandler)
# ---------------------------------------------------------------------------

SETPHOTO_WAITING = 1

@admin_only
async def setphoto_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("📸 Envoie la nouvelle photo de bienvenue :")
    return SETPHOTO_WAITING


async def setphoto_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("⚠️ Envoie une image, pas autre chose.")
        return SETPHOTO_WAITING

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    await file.download_to_drive(PHOTO_PATH)
    await update.message.reply_text("✅ Photo de bienvenue mise à jour !")
    return ConversationHandler.END

# ---------------------------------------------------------------------------
# /broadcast  (ConversationHandler)
# ---------------------------------------------------------------------------

BROADCAST_WAITING = 1

@admin_only
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    count = get_user_count()
    await update.message.reply_text(
        f"📣 Envoie le message ou la photo à diffuser à *{count}* utilisateurs.\n"
        "(/annuler pour annuler)",
        parse_mode="Markdown",
    )
    return BROADCAST_WAITING


async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_ids = get_all_user_ids()
    success, failed = 0, 0

    for uid in user_ids:
        try:
            if update.message.photo:
                photo = update.message.photo[-1]
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=photo.file_id,
                    caption=update.message.caption or "",
                )
            else:
                await context.bot.send_message(chat_id=uid, text=update.message.text)
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await update.message.reply_text(
        f"✅ Diffusion terminée !\n📨 Envoyés : {success}\n❌ Échecs : {failed}"
    )
    return ConversationHandler.END

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("settext", settext_start)],
        states={SETTEXT_WAITING: [MessageHandler(filters.TEXT & ~filters.COMMAND, settext_receive)]},
        fallbacks=[CommandHandler("annuler", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setlink", setlink_start)],
        states={SETLINK_WAITING: [MessageHandler(filters.TEXT & ~filters.COMMAND, setlink_receive)]},
        fallbacks=[CommandHandler("annuler", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setphoto", setphoto_start)],
        states={SETPHOTO_WAITING: [MessageHandler(filters.PHOTO, setphoto_receive)]},
        fallbacks=[CommandHandler("annuler", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={BROADCAST_WAITING: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send),
            MessageHandler(filters.PHOTO, broadcast_send),
        ]},
        fallbacks=[CommandHandler("annuler", cancel)],
    ))

    logging.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
