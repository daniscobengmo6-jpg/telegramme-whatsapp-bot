import os
import glob
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def find_banner() -> str | None:
    for ext in ("jpg", "jpeg", "png", "webp", "gif"):
        matches = glob.glob(os.path.join(BASE_DIR, f"banner.{ext}"))
        if matches:
            return matches[0]
    return None

CHECKING_MESSAGE = (
    "🔄 Vérification de votre accès...\n"
    "⏳ Veuillez patienter..."
)

WELCOME_MESSAGE = (
    "🎁 Bienvenue !\n\n"
    "⚽ Tu veux recevoir les scores exacts, les coupons VIP et les analyses avant tout le monde ?\n\n"
    "👇 Appuie sur le bouton ci-dessous pour rejoindre gratuitement notre chaîne WhatsApp."
)

WHATSAPP_BUTTON = InlineKeyboardMarkup([
    [InlineKeyboardButton(
        "📲 Rejoindre la chaîne WhatsApp",
        url="https://whatsapp.com/channel/0029VbC1Xd4C6ZvgosLcF531"
    )]
])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(CHECKING_MESSAGE)
    await asyncio.sleep(2)
    banner = find_banner()
    if banner:
        with open(banner, "rb") as img:
            await update.message.reply_photo(
                photo=img,
                caption=WELCOME_MESSAGE,
                reply_markup=WHATSAPP_BUTTON,
            )
    else:
        await update.message.reply_text(WELCOME_MESSAGE, reply_markup=WHATSAPP_BUTTON)


def main() -> None:
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    logging.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
