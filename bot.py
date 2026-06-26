"""Telegram bot: tap a range → get all episodes forwarded instantly.

Replicates @OnePiece_MMSub_s1_bot behavior:
- Range-based buttons: (1-25), (26-50), etc.
- Clicking a range forwards all episodes in that range from the storage channel
- Myanmar language button labels matching the reference bot

Forwarding from a channel has NO size limit (unlike bot uploads capped at 50MB),
so even 316MB episodes work perfectly.
"""
import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import config
import library

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("bot")

# ── Globals (loaded once at startup) ────────────────────────────────
EPISODES: dict[int, library.Episode] = {}
RANGES: list[library.EpisodeRange] = []
CHANNEL_ID: int = 0


def reload_data() -> None:
    """(Re)load episode data from manifest files."""
    global EPISODES, RANGES, CHANNEL_ID
    EPISODES = library.load_manifest()
    RANGES = library.get_ranges(EPISODES)
    CHANNEL_ID = library.get_channel_id()
    log.info(
        "Loaded %d episodes in %d ranges. Channel: %s",
        len(EPISODES), len(RANGES), CHANNEL_ID or "(not set)",
    )


def _authorized(update: Update) -> bool:
    if not config.ALLOWED_USER_IDS:
        return True
    user = update.effective_user
    return bool(user and user.id in config.ALLOWED_USER_IDS)


def _main_menu_markup() -> InlineKeyboardMarkup:
    """Build the main menu with range buttons."""
    rows = []
    for r in RANGES:
        rows.append([InlineKeyboardButton(r.label, callback_data=r.callback)])
    return InlineKeyboardMarkup(rows)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — show range menu."""
    if not _authorized(update):
        await update.message.reply_text("Sorry, you're not on the allow list.")
        return

    reload_data()

    if not EPISODES:
        await update.message.reply_text(
            "No episodes loaded yet.\n\n"
            "Run the downloader and uploader first, then /start again."
        )
        return

    total = library.get_total_episodes(EPISODES)
    await update.message.reply_text(
        f"🏴‍☠️ One Piece MMSub — {total} Episodes\n"
        f"ကြည့်ချင်တဲ့ အပိုင်းကို နှိပ်ပါ။",
        reply_markup=_main_menu_markup(),
    )


async def cmd_reload(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reload — refresh episode data from manifest files."""
    if not _authorized(update):
        return
    reload_data()
    await update.message.reply_text(
        f"✅ Reloaded: {len(EPISODES)} episodes in {len(RANGES)} ranges."
    )


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button presses."""
    query = update.callback_query
    await query.answer()

    if not _authorized(update):
        await query.edit_message_text("Sorry, you're not on the allow list.")
        return

    data = query.data or ""

    # ── Range button: forward all episodes in range ──────────────
    if data.startswith("range:"):
        parts = data.split(":")
        start, end = int(parts[1]), int(parts[2])
        await _forward_range(update, ctx, start, end)
        return

    # ── Single episode: forward just one ─────────────────────────
    if data.startswith("ep:"):
        ep_num = int(data.split(":")[1])
        await _forward_episode(update, ctx, ep_num)
        return

    # ── Back to main menu ────────────────────────────────────────
    if data == "menu":
        reload_data()
        await query.edit_message_text(
            f"🏴‍☠️ One Piece MMSub — {library.get_total_episodes(EPISODES)} Episodes\n"
            f"ကြည့်ချင်တဲ့ အပိုင်းကို နှိပ်ပါ။",
            reply_markup=_main_menu_markup(),
        )
        return


async def _forward_range(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE,
    start: int, end: int,
) -> None:
    """Forward all episodes in a range from the storage channel."""
    chat_id = update.effective_chat.id

    if not CHANNEL_ID:
        await ctx.bot.send_message(
            chat_id,
            "❌ Storage channel not configured.\n"
            "Run uploader.py first and set STORAGE_CHANNEL_ID in .env"
        )
        return

    episodes = library.get_episodes_in_range(EPISODES, start, end)
    if not episodes:
        await ctx.bot.send_message(chat_id, f"No episodes found in range {start}-{end}.")
        return

    msg_ids = library.get_message_ids(episodes)
    if not msg_ids:
        await ctx.bot.send_message(
            chat_id,
            f"Episodes {start}-{end} don't have channel message IDs.\n"
            "Run uploader.py to upload them to the storage channel."
        )
        return

    # Send a status message
    status = await ctx.bot.send_message(
        chat_id,
        f"📤 Forwarding episodes {start} - {end} ({len(msg_ids)} videos)...",
    )

    # Forward all at once using forward_messages (batch)
    try:
        await ctx.bot.forward_messages(
            chat_id=chat_id,
            from_chat_id=CHANNEL_ID,
            message_ids=msg_ids,
        )
        log.info("Forwarded %d videos (Ep %d-%d) to chat %d", len(msg_ids), start, end, chat_id)
    except Exception as exc:
        log.exception("Failed to forward range %d-%d", start, end)

        # Fallback: try one by one
        await ctx.bot.send_message(
            chat_id,
            f"⚠️ Batch forward failed ({exc}). Sending one by one..."
        )
        sent = 0
        for ep in episodes:
            if ep.message_id:
                try:
                    await ctx.bot.forward_message(
                        chat_id=chat_id,
                        from_chat_id=CHANNEL_ID,
                        message_id=ep.message_id,
                    )
                    sent += 1
                    await asyncio.sleep(0.3)  # tiny delay to avoid flood
                except Exception as e2:
                    log.warning("Failed Ep %d: %s", ep.number, e2)
                    await ctx.bot.send_message(chat_id, f"❌ Ep {ep.number}: {e2}")

        await ctx.bot.send_message(chat_id, f"✅ Sent {sent}/{len(episodes)} videos.")
        return

    # Clean up status message (optional)
    try:
        await status.delete()
    except Exception:
        pass


async def _forward_episode(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE,
    ep_num: int,
) -> None:
    """Forward a single episode from the storage channel."""
    chat_id = update.effective_chat.id

    if not CHANNEL_ID:
        await ctx.bot.send_message(chat_id, "❌ Storage channel not configured.")
        return

    ep = EPISODES.get(ep_num)
    if not ep or not ep.message_id:
        await ctx.bot.send_message(chat_id, f"Episode {ep_num} not available.")
        return

    try:
        await ctx.bot.forward_message(
            chat_id=chat_id,
            from_chat_id=CHANNEL_ID,
            message_id=ep.message_id,
        )
    except Exception as exc:
        log.exception("Failed to forward Ep %d", ep_num)
        await ctx.bot.send_message(chat_id, f"❌ Failed: {exc}")


def main() -> None:
    config.validate()
    reload_data()

    app = Application.builder().token(config.BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("list", cmd_start))
    app.add_handler(CommandHandler("reload", cmd_reload))

    # Button callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    log.info("🏴‍☠️ One Piece Bot started!")
    log.info("   Episodes: %d | Ranges: %d | Channel: %s",
             len(EPISODES), len(RANGES), CHANNEL_ID or "NOT SET")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
