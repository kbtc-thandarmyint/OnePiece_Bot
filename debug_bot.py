"""Debug: click one button on the reference bot and inspect what comes back."""
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SOURCE_BOT = os.getenv("SOURCE_BOT", "OnePiece_MMSub_s1_bot")
SESSION_FILE = "downloader_session"


async def run():
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()
    me = await client.get_me()
    print(f"✅ Logged in as {me.first_name}")

    bot_entity = await client.get_entity(SOURCE_BOT)
    print(f"🤖 Bot: {bot_entity.first_name}")

    # Send /start
    print("\n📤 Sending /start...")
    await client.send_message(bot_entity, "/start")
    await asyncio.sleep(3)

    # Get message with buttons
    messages = await client.get_messages(bot_entity, limit=5)
    button_msg = None
    for msg in messages:
        if msg.buttons:
            button_msg = msg
            break

    if not button_msg:
        print("❌ No buttons found")
        await client.disconnect()
        return

    print(f"\n🎮 Buttons found. Clicking row[2] = '( 1 - 25 )'...")

    # Record last message ID before clicking
    last_msgs = await client.get_messages(bot_entity, limit=1)
    last_id = last_msgs[0].id if last_msgs else 0
    print(f"   Last msg ID before click: {last_id}")

    # Click the (1-25) button (row 2, col 0)
    try:
        result = await button_msg.click(2, 0)
        print(f"   Click result type: {type(result)}")
        if result:
            print(f"   Click result: {result}")
    except Exception as e:
        print(f"   Click error: {e}")

    # Wait for messages
    print(f"\n⏳ Waiting 15s for responses...")
    await asyncio.sleep(15)

    # Check ALL new messages
    print(f"\n📬 Checking new messages (after ID {last_id})...")
    all_new = []
    async for msg in client.iter_messages(bot_entity, min_id=last_id, limit=100):
        if msg.id > last_id:
            all_new.append(msg)

    print(f"   Found {len(all_new)} new messages\n")

    for msg in sorted(all_new, key=lambda m: m.id):
        print(f"   MSG #{msg.id}:")
        print(f"     text: {(msg.text or msg.message or '(none)')[:100]}")
        print(f"     media: {type(msg.media).__name__ if msg.media else 'None'}")
        print(f"     fwd_from: {msg.fwd_from}")
        if msg.media:
            print(f"     media details: {msg.media}")
        if msg.document:
            print(f"     document: mime={msg.document.mime_type}, size={msg.document.size}")
        if msg.buttons:
            print(f"     buttons: {[[b.text for b in row] for row in msg.buttons]}")
        print()

    # Also try getting messages from the dialog differently
    print(f"\n📬 Also checking via get_messages(limit=30)...")
    recent = await client.get_messages(bot_entity, limit=30)
    new_recent = [m for m in recent if m.id > last_id]
    print(f"   Found {len(new_recent)} messages after our click")
    for msg in new_recent[:5]:
        print(f"   MSG #{msg.id}: text={msg.text[:80] if msg.text else '(media)'}, media={type(msg.media).__name__ if msg.media else 'None'}")

    await client.disconnect()
    print("\n👋 Done!")


if __name__ == "__main__":
    asyncio.run(run())
