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

TOKEN      = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_ID   = 7777462320
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.environ.get("DB_PATH",    os.path.join(BASE_DIR, "bot_data.db"))
PHOTO_PATH = os.environ.get("PHOTO_PATH", os.path.join(BASE_DIR, "banner.jpg"))

BOT_USERNAME = None  # set at startup via post_init

# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        # ── settings table ──────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # ── users table ─────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                referred_by INTEGER,
                joined_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        # migrate: add referred_by if the table existed before this column
        try:
            conn.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        except sqlite3.OperationalError:
            pass  # column already exists

        # ── buttons table ───────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS buttons (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                text     TEXT NOT NULL,
                url      TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0
            )
        """)

        # ── default settings ─────────────────────────────────────────────────
        defaults = {
            "title": "🎁 Bienvenue !",
            "welcome_text": (
                "⚽ Tu veux recevoir les scores exacts, les coupons VIP et les analyses avant tout le monde ?\n\n"
                "👇 Appuie sur le bouton ci-dessous pour rejoindre gratuitement notre chaîne WhatsApp."
            ),
            "whatsapp_url": "https://whatsapp.com/channel/0029VbC1Xd4C6ZvgosLcF531",
            "delay": "2",
        }
        for k, v in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

        # ── migrate old welcome_text that included title on first line ────────
        row = conn.execute("SELECT value FROM settings WHERE key='welcome_text'").fetchone()
        if row and row["value"].startswith("🎁 Bienvenue !"):
            body = row["value"].split("\n\n", 1)[1] if "\n\n" in row["value"] else row["value"]
            conn.execute("UPDATE settings SET value=? WHERE key='welcome_text'", (body,))

        # ── seed buttons table from legacy whatsapp_url if empty ─────────────
        btn_count = conn.execute("SELECT COUNT(*) FROM buttons").fetchone()[0]
        if btn_count == 0:
            url_row = conn.execute("SELECT value FROM settings WHERE key='whatsapp_url'").fetchone()
            url = url_row["value"] if url_row else "https://whatsapp.com/channel/0029VbC1Xd4C6ZvgosLcF531"
            conn.execute(
                "INSERT INTO buttons (text, url, position) VALUES (?, ?, ?)",
                ("📲 Rejoindre la chaîne WhatsApp", url, 0),
            )

        conn.commit()


# ── settings helpers ─────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


# ── buttons helpers ───────────────────────────────────────────────────────────

def get_buttons() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT id, text, url FROM buttons ORDER BY position, id").fetchall()
        return [dict(r) for r in rows]


def build_keyboard() -> InlineKeyboardMarkup:
    btns = get_buttons()
    return InlineKeyboardMarkup([[InlineKeyboardButton(b["text"], url=b["url"])] for b in btns])


def add_button(text: str, url: str):
    with get_db() as conn:
        pos = conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM buttons").fetchone()[0]
        conn.execute("INSERT INTO buttons (text, url, position) VALUES (?, ?, ?)", (text, url, pos))
        conn.commit()


def update_button_text(btn_id: int, text: str):
    with get_db() as conn:
        conn.execute("UPDATE buttons SET text=? WHERE id=?", (text, btn_id))
        conn.commit()


def update_button_url(btn_id: int, url: str):
    with get_db() as conn:
        conn.execute("UPDATE buttons SET url=? WHERE id=?", (url, btn_id))
        conn.commit()


def delete_button(btn_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM buttons WHERE id=?", (btn_id,))
        conn.commit()


def format_buttons_list(buttons: list[dict]) -> str:
    if not buttons:
        return "Aucun bouton configuré."
    lines = []
    for i, b in enumerate(buttons, 1):
        lines.append(f"{i}. {b['text']}\n   🔗 {b['url']}")
    return "\n\n".join(lines)


# ── user helpers ──────────────────────────────────────────────────────────────

def register_user(user_id: int, username: str | None, referred_by: int | None = None) -> bool:
    with get_db() as conn:
        if conn.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone():
            return False
        conn.execute(
            "INSERT INTO users (user_id, username, referred_by) VALUES (?, ?, ?)",
            (user_id, username, referred_by),
        )
        conn.commit()
        return True


def get_all_user_ids() -> list[int]:
    with get_db() as conn:
        return [r["user_id"] for r in conn.execute("SELECT user_id FROM users").fetchall()]


def get_user_count() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]


def get_referral_stats() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT u.referred_by,
                   COUNT(*) as cnt,
                   (SELECT username FROM users WHERE user_id = u.referred_by) as ref_name
            FROM users u
            WHERE u.referred_by IS NOT NULL
            GROUP BY u.referred_by
            ORDER BY cnt DESC LIMIT 10
        """).fetchall()
        return [dict(r) for r in rows]


def get_user_referral_count(user_id: int) -> int:
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE referred_by=?", (user_id,)
        ).fetchone()["cnt"]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

CHECKING_MESSAGE = "🔄 Vérification de votre accès...\n⏳ Veuillez patienter..."


def find_banner() -> str | None:
    photo_dir = os.path.dirname(PHOTO_PATH)
    for ext in ("jpg", "jpeg", "png", "webp", "gif"):
        matches = glob.glob(os.path.join(photo_dir, f"banner.{ext}"))
        if matches:
            return matches[0]
    if os.path.exists(PHOTO_PATH):
        return PHOTO_PATH
    return None


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Accès refusé.")
            return ConversationHandler.END
        return await func(update, context)
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    referred_by = None
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                ref_id = int(arg[4:])
                if ref_id != user.id:
                    referred_by = ref_id
            except ValueError:
                pass

    is_new = register_user(user.id, user.username, referred_by)

    if is_new and referred_by:
        count = get_user_referral_count(referred_by)
        try:
            await context.bot.send_message(
                chat_id=referred_by,
                text=f"🎉 Quelqu'un a rejoint via ton lien ! Tu as maintenant *{count}* filleul(s).",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    await update.message.reply_text(CHECKING_MESSAGE)
    delay = float(get_setting("delay", "2"))
    await asyncio.sleep(delay)

    title        = get_setting("title")
    welcome_text = get_setting("welcome_text")
    caption      = f"{title}\n\n{welcome_text}" if title else welcome_text
    keyboard     = build_keyboard()

    banner = find_banner()
    if banner:
        with open(banner, "rb") as img:
            await update.message.reply_photo(photo=img, caption=caption, reply_markup=keyboard)
    else:
        await update.message.reply_text(caption, reply_markup=keyboard)


# ─────────────────────────────────────────────────────────────────────────────
# /myreferral
# ─────────────────────────────────────────────────────────────────────────────

async def myreferral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user  = update.effective_user
    count = get_user_referral_count(user.id)
    link  = f"https://t.me/{BOT_USERNAME}?start=ref_{user.id}"
    await update.message.reply_text(
        f"🔗 Ton lien de parrainage :\n{link}\n\n"
        f"👥 Filleuls recrutés : *{count}*\n\n"
        "Partage ce lien — tu seras notifié à chaque fois que quelqu'un le rejoint !",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────────────────────────────────────
# /stats
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"📊 Utilisateurs total : *{get_user_count()}*", parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /refstats
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def refstats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = get_referral_stats()
    if not rows:
        await update.message.reply_text("📭 Aucun parrainage enregistré pour l'instant.")
        return
    lines = ["🏆 *Top parrains :*\n"]
    for i, r in enumerate(rows, 1):
        name = f"@{r['ref_name']}" if r["ref_name"] else f"ID {r['referred_by']}"
        lines.append(f"{i}. {name} — *{r['cnt']}* filleul(s)")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# /preview  (admin — see the welcome message exactly as users will)
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    title        = get_setting("title")
    welcome_text = get_setting("welcome_text")
    caption      = f"{title}\n\n{welcome_text}" if title else welcome_text
    keyboard     = build_keyboard()
    delay        = get_setting("delay", "2")

    await update.message.reply_text(
        f"👁 *Aperçu du message de bienvenue* (délai actuel : {delay}s)\n\n"
        "Voici exactement ce que verront vos utilisateurs :",
        parse_mode="Markdown",
    )

    banner = find_banner()
    if banner:
        with open(banner, "rb") as img:
            await update.message.reply_photo(photo=img, caption=caption, reply_markup=keyboard)
    else:
        await update.message.reply_text(caption, reply_markup=keyboard)


# ─────────────────────────────────────────────────────────────────────────────
# /listbuttons
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def listbuttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    buttons = get_buttons()
    await update.message.reply_text(
        f"🔘 *Boutons actuels :*\n\n{format_buttons_list(buttons)}",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────────────────────────────────────
# /settext
# ─────────────────────────────────────────────────────────────────────────────

SETTEXT_STEP = 1

@admin_only
async def settext_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        f"✏️ *Texte actuel :*\n\n{get_setting('welcome_text')}\n\nEnvoie le nouveau texte :",
        parse_mode="Markdown",
    )
    return SETTEXT_STEP

async def settext_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    set_setting("welcome_text", update.message.text)
    await update.message.reply_text("✅ Texte de bienvenue mis à jour !")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /settitle
# ─────────────────────────────────────────────────────────────────────────────

SETTITLE_STEP = 1

@admin_only
async def settitle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        f"🏷 *Titre actuel :*\n\n{get_setting('title')}\n\nEnvoie le nouveau titre :",
        parse_mode="Markdown",
    )
    return SETTITLE_STEP

async def settitle_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    set_setting("title", update.message.text)
    await update.message.reply_text("✅ Titre mis à jour !")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /setdelay
# ─────────────────────────────────────────────────────────────────────────────

SETDELAY_STEP = 1

@admin_only
async def setdelay_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        f"⏱ *Délai actuel :* {get_setting('delay', '2')} seconde(s)\n\nEnvoie le nouveau délai (en secondes, ex: 3) :",
        parse_mode="Markdown",
    )
    return SETDELAY_STEP

async def setdelay_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text.strip())
        if val < 0 or val > 10:
            raise ValueError
        set_setting("delay", str(val))
        await update.message.reply_text(f"✅ Délai mis à jour : {val} seconde(s) !")
    except ValueError:
        await update.message.reply_text("⚠️ Envoie un nombre entre 0 et 10 (ex: 2 ou 1.5).")
        return SETDELAY_STEP
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /setlink  (updates first button URL — backward compat)
# ─────────────────────────────────────────────────────────────────────────────

SETLINK_STEP = 1

@admin_only
async def setlink_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    buttons = get_buttons()
    first_url = buttons[0]["url"] if buttons else ""
    await update.message.reply_text(
        f"🔗 *Lien actuel du premier bouton :*\n{first_url}\n\nEnvoie le nouveau lien :",
        parse_mode="Markdown",
    )
    return SETLINK_STEP

async def setlink_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_url = update.message.text.strip()
    if not new_url.startswith("http"):
        await update.message.reply_text("⚠️ Le lien doit commencer par http:// ou https://")
        return SETLINK_STEP
    buttons = get_buttons()
    if buttons:
        update_button_url(buttons[0]["id"], new_url)
    set_setting("whatsapp_url", new_url)
    await update.message.reply_text("✅ Lien mis à jour !")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /setbuttontext
# ─────────────────────────────────────────────────────────────────────────────

SBT_SELECT, SBT_TEXT = 1, 2

@admin_only
async def setbuttontext_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    buttons = get_buttons()
    if not buttons:
        await update.message.reply_text("⚠️ Aucun bouton à modifier.")
        return ConversationHandler.END
    await update.message.reply_text(
        f"🔘 *Boutons :*\n\n{format_buttons_list(buttons)}\n\nEnvoie le numéro du bouton à modifier :",
        parse_mode="Markdown",
    )
    context.user_data["sbt_buttons"] = buttons
    return SBT_SELECT

async def setbuttontext_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    buttons = context.user_data.get("sbt_buttons", [])
    try:
        idx = int(update.message.text.strip()) - 1
        if idx < 0 or idx >= len(buttons):
            raise ValueError
        context.user_data["sbt_btn_id"] = buttons[idx]["id"]
        await update.message.reply_text(
            f"✏️ Texte actuel : *{buttons[idx]['text']}*\n\nEnvoie le nouveau texte :",
            parse_mode="Markdown",
        )
        return SBT_TEXT
    except ValueError:
        await update.message.reply_text(f"⚠️ Envoie un numéro entre 1 et {len(buttons)}.")
        return SBT_SELECT

async def setbuttontext_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    update_button_text(context.user_data["sbt_btn_id"], update.message.text.strip())
    await update.message.reply_text("✅ Texte du bouton mis à jour !")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /setbuttonlink
# ─────────────────────────────────────────────────────────────────────────────

SBL_SELECT, SBL_URL = 1, 2

@admin_only
async def setbuttonlink_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    buttons = get_buttons()
    if not buttons:
        await update.message.reply_text("⚠️ Aucun bouton à modifier.")
        return ConversationHandler.END
    await update.message.reply_text(
        f"🔘 *Boutons :*\n\n{format_buttons_list(buttons)}\n\nEnvoie le numéro du bouton à modifier :",
        parse_mode="Markdown",
    )
    context.user_data["sbl_buttons"] = buttons
    return SBL_SELECT

async def setbuttonlink_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    buttons = context.user_data.get("sbl_buttons", [])
    try:
        idx = int(update.message.text.strip()) - 1
        if idx < 0 or idx >= len(buttons):
            raise ValueError
        context.user_data["sbl_btn_id"] = buttons[idx]["id"]
        await update.message.reply_text(
            f"🔗 URL actuelle :\n{buttons[idx]['url']}\n\nEnvoie la nouvelle URL :",
        )
        return SBL_URL
    except ValueError:
        await update.message.reply_text(f"⚠️ Envoie un numéro entre 1 et {len(buttons)}.")
        return SBL_SELECT

async def setbuttonlink_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_url = update.message.text.strip()
    if not new_url.startswith("http"):
        await update.message.reply_text("⚠️ Le lien doit commencer par http:// ou https://")
        return SBL_URL
    update_button_url(context.user_data["sbl_btn_id"], new_url)
    await update.message.reply_text("✅ URL du bouton mise à jour !")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /addbutton
# ─────────────────────────────────────────────────────────────────────────────

AB_TEXT, AB_URL = 1, 2

@admin_only
async def addbutton_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("➕ Envoie le texte du nouveau bouton :")
    return AB_TEXT

async def addbutton_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["ab_text"] = update.message.text.strip()
    await update.message.reply_text("🔗 Envoie l'URL du bouton :")
    return AB_URL

async def addbutton_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_url = update.message.text.strip()
    if not new_url.startswith("http"):
        await update.message.reply_text("⚠️ Le lien doit commencer par http:// ou https://")
        return AB_URL
    add_button(context.user_data["ab_text"], new_url)
    await update.message.reply_text(
        f"✅ Bouton ajouté !\n\n*{context.user_data['ab_text']}*\n{new_url}",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /removebutton
# ─────────────────────────────────────────────────────────────────────────────

RB_SELECT = 1

@admin_only
async def removebutton_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    buttons = get_buttons()
    if not buttons:
        await update.message.reply_text("⚠️ Aucun bouton à supprimer.")
        return ConversationHandler.END
    await update.message.reply_text(
        f"🗑 *Boutons :*\n\n{format_buttons_list(buttons)}\n\nEnvoie le numéro du bouton à supprimer :",
        parse_mode="Markdown",
    )
    context.user_data["rb_buttons"] = buttons
    return RB_SELECT

async def removebutton_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    buttons = context.user_data.get("rb_buttons", [])
    try:
        idx = int(update.message.text.strip()) - 1
        if idx < 0 or idx >= len(buttons):
            raise ValueError
        btn = buttons[idx]
        delete_button(btn["id"])
        await update.message.reply_text(f"✅ Bouton *{btn['text']}* supprimé !", parse_mode="Markdown")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text(f"⚠️ Envoie un numéro entre 1 et {len(buttons)}.")
        return RB_SELECT


# ─────────────────────────────────────────────────────────────────────────────
# /setphoto
# ─────────────────────────────────────────────────────────────────────────────

SETPHOTO_STEP = 1

@admin_only
async def setphoto_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("📸 Envoie la nouvelle photo de bienvenue :")
    return SETPHOTO_STEP

async def setphoto_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("⚠️ Envoie une image, pas autre chose.")
        return SETPHOTO_STEP
    file = await context.bot.get_file(update.message.photo[-1].file_id)
    await file.download_to_drive(PHOTO_PATH)
    await update.message.reply_text("✅ Photo de bienvenue mise à jour !")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /broadcast
# ─────────────────────────────────────────────────────────────────────────────

BROADCAST_STEP = 1

@admin_only
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        f"📣 Envoie le message ou la photo à diffuser à *{get_user_count()}* utilisateurs.\n"
        "(/annuler pour annuler)",
        parse_mode="Markdown",
    )
    return BROADCAST_STEP

async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_ids = get_all_user_ids()
    success = failed = 0
    for uid in user_ids:
        try:
            if update.message.photo:
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=update.message.photo[-1].file_id,
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


# ─────────────────────────────────────────────────────────────────────────────
# Cancel fallback (shared)
# ─────────────────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Annulé.")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def post_init(application):
    global BOT_USERNAME
    BOT_USERNAME = (await application.bot.get_me()).username
    logging.info(f"Bot username: @{BOT_USERNAME}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error("Exception while handling an update:", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Une erreur est survenue lors du traitement de cette commande."
            )
    except Exception:
        pass


def main() -> None:
    logging.info(f"Using SQLite database at: {DB_PATH}")
    logging.info(f"Database file exists before init: {os.path.exists(DB_PATH)}")
    init_db()
    with get_db() as conn:
        btn_count = conn.execute("SELECT COUNT(*) FROM buttons").fetchone()[0]
        logging.info(f"Buttons table has {btn_count} row(s) after init")

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    # ── public commands ────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myreferral", myreferral))

    # ── admin simple commands ──────────────────────────────────────────────
    app.add_handler(CommandHandler("stats",       stats))
    app.add_handler(CommandHandler("refstats",    refstats))
    app.add_handler(CommandHandler("listbuttons", listbuttons))
    app.add_handler(CommandHandler("preview",     preview))

    # ── admin conversation commands ────────────────────────────────────────
    CANCEL = [CommandHandler("annuler", cancel)]
    TEXT_NO_CMD = filters.TEXT & ~filters.COMMAND

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("settext", settext_start)],
        states={SETTEXT_STEP: [MessageHandler(TEXT_NO_CMD, settext_receive)]},
        fallbacks=CANCEL,
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("settitle", settitle_start)],
        states={SETTITLE_STEP: [MessageHandler(TEXT_NO_CMD, settitle_receive)]},
        fallbacks=CANCEL,
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setdelay", setdelay_start)],
        states={SETDELAY_STEP: [MessageHandler(TEXT_NO_CMD, setdelay_receive)]},
        fallbacks=CANCEL,
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setlink", setlink_start)],
        states={SETLINK_STEP: [MessageHandler(TEXT_NO_CMD, setlink_receive)]},
        fallbacks=CANCEL,
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setbuttontext", setbuttontext_start)],
        states={
            SBT_SELECT: [MessageHandler(TEXT_NO_CMD, setbuttontext_select)],
            SBT_TEXT:   [MessageHandler(TEXT_NO_CMD, setbuttontext_receive)],
        },
        fallbacks=CANCEL,
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setbuttonlink", setbuttonlink_start)],
        states={
            SBL_SELECT: [MessageHandler(TEXT_NO_CMD, setbuttonlink_select)],
            SBL_URL:    [MessageHandler(TEXT_NO_CMD, setbuttonlink_receive)],
        },
        fallbacks=CANCEL,
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("addbutton", addbutton_start)],
        states={
            AB_TEXT: [MessageHandler(TEXT_NO_CMD, addbutton_text)],
            AB_URL:  [MessageHandler(TEXT_NO_CMD, addbutton_url)],
        },
        fallbacks=CANCEL,
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("removebutton", removebutton_start)],
        states={RB_SELECT: [MessageHandler(TEXT_NO_CMD, removebutton_select)]},
        fallbacks=CANCEL,
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setphoto", setphoto_start)],
        states={SETPHOTO_STEP: [MessageHandler(filters.PHOTO, setphoto_receive)]},
        fallbacks=CANCEL,
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={BROADCAST_STEP: [
            MessageHandler(TEXT_NO_CMD, broadcast_send),
            MessageHandler(filters.PHOTO, broadcast_send),
        ]},
        fallbacks=CANCEL,
    ))

    app.add_error_handler(error_handler)

    logging.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
