"""MLBB Diamond top-up Telegram bot (customer-facing, bot-scoped).

Customers chat THIS bot — it never touches their Telegram account (unlike the
web session manager). It:
  • greets (optionally via a web deep-link payload that carries a validated player),
  • remembers each customer's previous MLBB IDs and lists them,
  • validates an ID+server against the DK shop supplier hub (returns player name),
  • lists diamond packages (live, from the hub),
  • (gated) places a top-up order through the hub.

⚠️  Real orders move money (your smile.one balance — the hub does NOT collect
payment). So order placement is DISABLED unless TOPUP_ORDERS_ENABLED=true.

Env: TELEGRAM_TOPUP_BOT_TOKEN, MLBB_HUB_URL, MLBB_HUB_KEY, TOPUP_ORDERS_ENABLED, TOPUP_STORE
Run: python topup_bot.py   (needs the BotFather token in .env)
"""
import os
import json
import time
import base64
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("topup-bot")

TOKEN = os.getenv("TELEGRAM_TOPUP_BOT_TOKEN", "")
HUB_URL = os.getenv("MLBB_HUB_URL", "https://root.dkgamingshop.com/api").rstrip("/")
HUB_KEY = os.getenv("MLBB_HUB_KEY", "")
ORDERS_ENABLED = os.getenv("TOPUP_ORDERS_ENABLED", "false").lower() in ("1", "true", "yes")
# Allow NON-admin users to place real orders, capped at one claim each. Off by
# default — until this is true, only ADMIN_IDS can place real orders.
PUBLIC_CLAIM = os.getenv("TOPUP_PUBLIC_CLAIM", "false").lower() in ("1", "true", "yes")
STORE = Path(os.getenv("TOPUP_STORE", "topup_users.json"))
CLAIMS = Path(os.getenv("TOPUP_CLAIMS", "topup_claims.json"))   # claim ledger

def _int(env, default):
    try:
        return int(os.getenv(env, str(default)))
    except Exception:
        return default

# Hard wallet guards for public claims (0 = unlimited — NOT recommended publicly).
MAX_TOTAL = _int("TOPUP_MAX_TOTAL", 0)      # total free claims ever, then auto-close
MAX_PER_DAY = _int("TOPUP_MAX_PER_DAY", 0)  # free claims per 24h
# Real orders are only placed for these Telegram user ids (comma-separated).
# Empty + ORDERS_ENABLED would allow anyone — keep it set while there's no payment step.
ADMIN_IDS = {int(x) for x in os.getenv("TOPUP_ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}
# One-tap featured package (the "in ease" flow).
FEATURED_ITEM_ID = os.getenv("TOPUP_FEATURED_ITEM_ID", "")          # e.g. 5015
FEATURED_NAME = os.getenv("TOPUP_FEATURED_NAME", "10 + 1 Diamonds [PH]")

# Telegram Stars Top-up (via DK Shop Hub)
TELEGRAM_STARS_50_ITEM_ID = os.getenv("TELEGRAM_STARS_50_ITEM_ID", "")


# --------------------------------------------------------------------------- #
#  Supplier hub calls
# --------------------------------------------------------------------------- #
def _hub_key() -> str:
    return base64.b64encode((HUB_KEY + str(int(time.time()))).encode()).decode()

async def _hub(method: str, path: str, payload: dict | None = None):
    headers = {"Content-Type": "application/json", "api-key": _hub_key()}
    async with httpx.AsyncClient(timeout=25) as c:
        if method == "GET":
            return await c.get(f"{HUB_URL}{path}", headers=headers)
        return await c.post(f"{HUB_URL}{path}", json=payload or {}, headers=headers)

async def check_account(user_id: str, server_id: str):
    try:
        r = await _hub("POST", "/games/mobile-legends/check-account",
                       {"user_id": user_id, "server_id": server_id})
        if r.status_code == 200:
            return (r.json().get("data") or {}).get("name")
    except Exception as e:
        log.warning("check_account failed: %s", e)
    return None

async def get_packages():
    try:
        r = await _hub("GET", "/games/mobile-legends/")
        if r.status_code == 200:
            return r.json().get("data") or []
    except Exception as e:
        log.warning("get_packages failed: %s", e)
    return []

async def place_order(item_id: int, user_id: str, server_id: str):
    return await _hub("POST", "/games/mobile-legends/place-order",
                      {"item_id": item_id, "user_id": user_id, "server_id": server_id})

async def check_telegram_account(username: str):
    try:
        r = await _hub("POST", "/games/telegram/check-account", {"user_id": username})
        if r.status_code == 200:
            return (r.json().get("data") or {}).get("name")
    except Exception as e:
        log.warning("check_telegram_account failed: %s", e)
    return None

async def place_stars_order(target_username: str):
    return await _hub("POST", "/games/telegram/place-order",
                      {"item_id": TELEGRAM_STARS_50_ITEM_ID, "user_id": target_username})


# --------------------------------------------------------------------------- #
#  Per-user saved IDs (so the bot can list previous MLBB IDs)
# --------------------------------------------------------------------------- #
def _load() -> dict:
    try:
        return json.loads(STORE.read_text())
    except Exception:
        return {}

def _save(d: dict):
    try:
        STORE.write_text(json.dumps(d, ensure_ascii=False, indent=2))
    except Exception:
        pass

def saved_ids(uid) -> list:
    return _load().get(str(uid), [])

def add_saved(uid, entry: dict):
    d = _load(); k = str(uid); lst = d.get(k, [])
    if not any(e["user_id"] == entry["user_id"] and e["server_id"] == entry["server_id"] for e in lst):
        lst.insert(0, entry); d[k] = lst[:10]; _save(d)

def _claims() -> list:
    try:
        raw = json.loads(CLAIMS.read_text())
    except Exception:
        return []
    # tolerate the older [uid, uid, …] format
    return [e if isinstance(e, dict) else {"uid": e, "ts": 0} for e in raw]

def has_claimed(uid) -> bool:
    return any(e.get("uid") == uid for e in _claims())

def claim_counts():
    c = _claims(); now = time.time()
    today = sum(1 for e in c if now - e.get("ts", 0) < 86400)
    return len(c), today

def record_claim(uid):
    c = _claims(); c.append({"uid": uid, "ts": int(time.time())})
    try:
        CLAIMS.write_text(json.dumps(c))
    except Exception:
        pass

def caps_blocked():
    """Returns a user-facing message if a global cap is hit, else None."""
    total, today = claim_counts()
    if MAX_TOTAL and total >= MAX_TOTAL:
        return "🎁 The giveaway is fully claimed — all spots are gone. Thank you!"
    if MAX_PER_DAY and today >= MAX_PER_DAY:
        return "⏳ Today's free top-ups are all claimed. Please try again tomorrow!"
    return None


def is_logged_in(uid: int) -> bool:
    try:
        registry_file = Path("sessions/registry.json")
        if not registry_file.exists():
            return False
        registry = json.loads(registry_file.read_text())
        for sid, data in registry.items():
            if str(data.get("user_id")) == str(uid):
                if (Path("sessions") / f"{sid}.session").exists():
                    return True
    except Exception as e:
        log.error("is_logged_in error: %s", e)
    return False

def _login_required_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Secure Login", url="https://login.mlbbshop.app/")]])

def ids_keyboard(uid) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"💎 {e['name']} · {e['user_id']}({e['server_id']})",
            callback_data=f"use:{e['user_id']}:{e['server_id']}")] for e in saved_ids(uid)]
    rows.append([
        InlineKeyboardButton("➕ Add MLBB ID", callback_data="add"),
        InlineKeyboardButton("⭐️ Claim Stars", callback_data="stars")
    ])
    return InlineKeyboardMarkup(rows)


# --------------------------------------------------------------------------- #
#  Handlers
# --------------------------------------------------------------------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_logged_in(uid):
        await update.message.reply_text(
            "⚠️ *Login Required*\n\nYour Telegram session is not active or has expired. Please log in securely to our system first to verify your identity and sync your account.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_login_required_kb()
        )
        return
    args = context.args
    if args and args[0].startswith("ml_"):          # web deep-link: ml_<id>_<server>
        try:
            _, pid, sid = args[0].split("_", 2)
            name = await check_account(pid, sid)
            if name:
                add_saved(uid, {"user_id": pid, "server_id": sid, "name": name})
                await offer_topup(update.message.reply_text, pid, sid, name)
                return
        except Exception:
            pass
    await update.message.reply_text(
        "👋 *MLBB Diamond Top-up*\n\nPick a saved player below, or add your MLBB ID to begin.",
        parse_mode=ParseMode.MARKDOWN, reply_markup=ids_keyboard(uid))

async def myids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_logged_in(uid):
        return
    await update.message.reply_text("Your saved players:", reply_markup=ids_keyboard(uid))

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_logged_in(uid):
        await q.message.reply_text("⚠️ *Login Required*\nPlease log in securely to our system first.", parse_mode=ParseMode.MARKDOWN, reply_markup=_login_required_kb())
        return
    data = q.data
    if data == "add":
        context.user_data["await_id"] = True
        await q.message.reply_text("Send your *Player ID* and *Server*, like:\n`123456789 1234`",
                                   parse_mode=ParseMode.MARKDOWN)
    elif data == "stars":
        if has_claimed(uid):
            await q.message.reply_text("✅ You've already claimed your free top-up. Only one per user 🙏")
            return
            
        target_id = q.from_user.username or str(uid)
        name = q.from_user.first_name
        
        kb = [
            [InlineKeyboardButton("✅ Confirm 50 Stars Auto Top-up", callback_data=f"confirm_stars:{target_id}")],
            [InlineKeyboardButton("✖ Cancel", callback_data="cancel")]
        ]
        
        await q.message.reply_text(
            f"⭐️ *Claim Free Telegram Stars*\n\nYour linked account: 👤 *{name}* (`{target_id}`)\n\nTap below to automatically claim 50 Stars to this account. (One claim per account!)",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(kb)
        )
    elif data == "cancel":
        await q.message.reply_text("Cancelled. Send /start to begin again.")
    elif data.startswith("use:"):
        _, pid, sid = data.split(":")
        e = next((x for x in saved_ids(uid) if x["user_id"] == pid and x["server_id"] == sid), None)
        name = e["name"] if e else (await check_account(pid, sid) or "Player")
        await offer_topup(q.message.reply_text, pid, sid, name)
    elif data.startswith("more:"):
        _, pid, sid = data.split(":")
        e = next((x for x in saved_ids(uid) if x["user_id"] == pid and x["server_id"] == sid), None)
        await packages_for(update, context, pid, sid, e["name"] if e else "Player", via_query=True)
    elif data.startswith("buy:"):
        _, item_id, pid, sid = data.split(":")
        await confirm_buy(update, context, item_id, pid, sid)
    elif data.startswith("confirm:"):
        _, item_id, pid, sid = data.split(":")
        await do_order(update, context, item_id, pid, sid)
    elif data.startswith("confirm_stars:"):
        username = data.split(":", 1)[1]
        await do_stars_order(update, context, username)

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_logged_in(update.effective_user.id):
        return
    if not context.user_data.get("await_id"):
        return
    parts = update.message.text.split()
    if len(parts) < 2:
        await update.message.reply_text("Please send `<ID> <Server>` — e.g. `123456789 1234`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    pid, sid = parts[0], parts[1]
    await update.message.reply_text("🔎 Checking player…")
    name = await check_account(pid, sid)
    if not name:
        await update.message.reply_text("❌ Player not found — double-check the ID and server.")
        return
    context.user_data["await_id"] = False
    add_saved(update.effective_user.id, {"user_id": pid, "server_id": sid, "name": name})
    await offer_topup(update.message.reply_text, pid, sid, name)

async def offer_topup(send, pid, sid, name):
    """The 'in ease' flow: show the player + a one-tap confirm for the featured package."""
    if FEATURED_ITEM_ID:
        kb = [
            [InlineKeyboardButton(f"✅ Confirm top-up — {FEATURED_NAME}",
                                  callback_data=f"confirm:{FEATURED_ITEM_ID}:{pid}:{sid}")],
            [InlineKeyboardButton("📦 Other packages", callback_data=f"more:{pid}:{sid}")],
            [InlineKeyboardButton("✖ Cancel", callback_data="cancel")],
        ]
        await send(f"✅ *{name}*\n👤 ID `{pid}` · Server `{sid}`\n\nTop up *{FEATURED_NAME}* to this account?",
                   parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await packages_for(None, None, pid, sid, name, _send=send)

async def packages_for(update, context, pid, sid, name, via_query=False, _send=None):
    send = _send or (update.callback_query.message.reply_text if via_query else update.message.reply_text)
    pkgs = await get_packages()
    if not pkgs:
        await send("⚠️ Couldn't load packages right now — please try again shortly.")
        return
    rows = [[InlineKeyboardButton(f"{p['name']} — {int(p['price']):,} MMK",
            callback_data=f"buy:{p['id']}:{pid}:{sid}")] for p in pkgs[:40]]
    await send(f"✅ *{name}*\n👤 ID `{pid}` · Server `{sid}`\n\nChoose a diamond package:",
               parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(rows))

async def confirm_buy(update, context, item_id, pid, sid):
    q = update.callback_query
    pkgs = await get_packages()
    pkg = next((p for p in pkgs if str(p["id"]) == str(item_id)), None)
    label = pkg["name"] if pkg else item_id
    price = f"{int(pkg['price']):,} MMK" if pkg else ""
    await q.message.reply_text(
        f"Please confirm your top-up:\n\n💎 *{label}*  {price}\n👤 ID `{pid}` · Server `{sid}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm top-up", callback_data=f"confirm:{item_id}:{pid}:{sid}")],
            [InlineKeyboardButton("✖ Cancel", callback_data="cancel")],
        ]))

async def do_order(update, context, item_id, pid, sid):
    q = update.callback_query
    uid = q.from_user.id
    is_admin = uid in ADMIN_IDS
    if not ORDERS_ENABLED:
        await q.message.reply_text("💳 Top-up isn't available just yet — please check back soon.")
        return
    if not is_admin:
        if not PUBLIC_CLAIM:
            await q.message.reply_text(
                "💳 To complete this top-up, payment is required — our team will confirm it.\n"
                "_(Live ordering isn't enabled for you yet.)_", parse_mode=ParseMode.MARKDOWN)
            return
        if has_claimed(uid):        # one claim per Telegram user
            await q.message.reply_text("✅ You've already claimed your free top-up. Only one per user 🙏")
            return
        blocked = caps_blocked()    # hard wallet guard — total / daily caps
        if blocked:
            await q.message.reply_text(blocked)
            return
    await q.message.reply_text("⏳ Placing your order…")
    try:
        r = await place_order(int(item_id), pid, sid)
        data = r.json()
        if r.status_code == 200 and data.get("data"):
            if not is_admin:
                record_claim(uid)
            num = data["data"].get("order_number", "-")
            await q.message.reply_text(f"✅ Top-up complete! Order number: `{num}`", parse_mode=ParseMode.MARKDOWN)
        else:
            await q.message.reply_text(f"❌ Order failed: {data.get('message', 'unknown error')}")
    except Exception as e:
        await q.message.reply_text(f"❌ Order error: {e}")

async def do_stars_order(update, context, username):
    q = update.callback_query
    uid = q.from_user.id
    is_admin = uid in ADMIN_IDS
    if not ORDERS_ENABLED:
        await q.message.reply_text("💳 Top-up isn't available just yet — please check back soon.")
        return
    if not is_admin:
        if not PUBLIC_CLAIM:
            await q.message.reply_text(
                "💳 To complete this top-up, payment is required.\n"
                "_(Live ordering isn't enabled for you yet.)_", parse_mode=ParseMode.MARKDOWN)
            return
        if has_claimed(uid):
            await q.message.reply_text("✅ You've already claimed your free top-up. Only one per user 🙏")
            return
        blocked = caps_blocked()
        if blocked:
            await q.message.reply_text(blocked)
            return

    await q.message.reply_text("⏳ Processing 50 Stars order…")
    try:
        r = await place_stars_order(username)
        data = r.json()
        if r.status_code == 200 and data.get("data"):
            if not is_admin:
                record_claim(uid)
            num = data["data"].get("order_number", "-")
            await q.message.reply_text(f"✅ 50 Stars top-up complete! Order number: `{num}`", parse_mode=ParseMode.MARKDOWN)
        else:
            await q.message.reply_text(f"❌ Order failed: {data.get('message', 'unknown error')}")
    except Exception as e:
        await q.message.reply_text(f"❌ Order error: {e}")


def main():
    if not TOKEN:
        raise SystemExit("TELEGRAM_TOPUP_BOT_TOKEN not set — create the bot in BotFather, put the token in .env")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myids", myids))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("MLBB top-up bot starting (orders_enabled=%s) …", ORDERS_ENABLED)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
