"""Download every video from @OnePiece_MMSub_s1_bot.

The reference bot uses a two-step flow:
1.  Click a range button → bot replies with a deep-link URL
    (e.g. https://t.me/Bot?start=UUID) that expires in 30s
2.  Click/send that deep link → bot actually sends the videos

This script automates both steps via Telethon.

Usage:
    python downloader.py               # full download
    python downloader.py --dry-run     # just show buttons, don't download
    python downloader.py --range 2     # only click button at row index 2
"""
import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaWebPage,
)

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SOURCE_BOT = os.getenv("SOURCE_BOT", "OnePiece_MMSub_s1_bot")
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "./media")).expanduser().resolve()
SESSION_FILE = "downloader_session"

# Timing
WAIT_AFTER_CLICK = 5        # wait after clicking range button (for deep link)
WAIT_AFTER_DEEPLINK = 60    # wait after sending deep link (for videos to arrive)
DELAY_BETWEEN_RANGES = 10   # pause between ranges to avoid rate limits


def sanitize_filename(caption: str) -> str:
    """Turn a caption like 'One Piece Ep-35' into 'One_Piece_Ep-35'."""
    name = caption.strip()
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', '_', name)
    return name


def extract_episode_number(text: str) -> int:
    """Try to pull an episode number from caption text."""
    m = re.search(r'[Ee]p[.-]?\s*(\d+)', text)
    if m:
        return int(m.group(1))
    # fallback: last number in the string
    nums = re.findall(r'\d+', text)
    return int(nums[-1]) if nums else 0


def extract_deeplink(messages: list) -> str | None:
    """Find a deep link URL in bot messages."""
    for msg in messages:
        text = msg.text or msg.message or ""
        # Look for t.me/BotName?start=XXXXX pattern
        m = re.search(r'https?://t\.me/\w+\?start=([a-zA-Z0-9_-]+)', text)
        if m:
            return m.group(1)  # return the start parameter
        # Also check webpage media
        if msg.media and isinstance(msg.media, MessageMediaWebPage):
            url = getattr(msg.media.webpage, 'url', '') or ''
            m = re.search(r'https?://t\.me/\w+\?start=([a-zA-Z0-9_-]+)', url)
            if m:
                return m.group(1)
    return None


async def run(dry_run: bool = False, only_range: int | None = None):
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"📡 Connecting to Telegram as your user account...")
    print(f"   Source bot: @{SOURCE_BOT}")
    print(f"   Download dir: {MEDIA_DIR}")
    print()

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()

    me = await client.get_me()
    print(f"✅ Logged in as {me.first_name} ({me.username or me.phone})")
    print()

    # Resolve the bot
    bot_entity = await client.get_entity(SOURCE_BOT)
    print(f"🤖 Found bot: {bot_entity.first_name} (@{bot_entity.username})")

    # Send /start and get the reply with buttons
    print("📤 Sending /start...")
    await client.send_message(bot_entity, "/start")
    await asyncio.sleep(3)

    # Get the most recent messages to find the one with buttons
    messages = await client.get_messages(bot_entity, limit=5)

    button_msg = None
    for msg in messages:
        if msg.buttons:
            button_msg = msg
            break

    if not button_msg:
        print("❌ No message with buttons found.")
        await client.disconnect()
        return

    # Display all buttons
    print(f"\n🎮 Found {len(button_msg.buttons)} rows of buttons:")
    all_buttons = []
    for row_idx, row in enumerate(button_msg.buttons):
        for btn_idx, btn in enumerate(row):
            marker = " 👈" if only_range is not None and row_idx == only_range else ""
            print(f"   [{row_idx}] {btn.text}{marker}")
            all_buttons.append((row_idx, btn_idx, btn.text))

    if dry_run:
        print("\n🏁 Dry run — stopping here.")
        await client.disconnect()
        return

    # Manifest to track everything
    manifest = {}
    manifest_path = Path("manifest.json")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        print(f"\n📋 Loaded existing manifest with {len(manifest)} entries")

    # Process each range button
    for row_idx, btn_idx, btn_text in all_buttons:
        # Skip if only processing a specific range
        if only_range is not None and row_idx != only_range:
            continue

        # Only process buttons that contain numbers (range buttons)
        if not re.search(r'\d', btn_text):
            print(f"\n⏭️  Skipping non-range button: {btn_text}")
            continue

        print(f"\n{'='*60}")
        print(f"🔘 STEP 1: Clicking range button [{row_idx}]: {btn_text}")
        print(f"{'='*60}")

        # Record last message ID
        msgs_before = await client.get_messages(bot_entity, limit=1)
        last_id_before = msgs_before[0].id if msgs_before else 0

        # Click the button
        try:
            await button_msg.click(row_idx, btn_idx)
        except Exception as e:
            print(f"   ⚠️  Click failed: {e}")
            continue

        # Wait for deep link response
        print(f"   ⏳ Waiting {WAIT_AFTER_CLICK}s for deep link...")
        await asyncio.sleep(WAIT_AFTER_CLICK)

        # Fetch new messages to find the deep link
        new_messages = []
        async for msg in client.iter_messages(bot_entity, min_id=last_id_before, limit=20):
            if msg.id > last_id_before:
                new_messages.append(msg)

        if not new_messages:
            print(f"   ❌ No response from bot after clicking")
            continue

        # Extract the deep link parameter
        start_param = extract_deeplink(new_messages)

        if not start_param:
            print(f"   ❌ No deep link found in response. Messages:")
            for m in new_messages:
                print(f"      - {(m.text or '(media)')[:80]}")
            continue

        print(f"   ✅ Found deep link param: {start_param[:20]}...")

        # STEP 2: Send /start with the deep link parameter
        print(f"\n   🔘 STEP 2: Sending /start {start_param[:20]}...")

        # Record last message ID again
        msgs_before2 = await client.get_messages(bot_entity, limit=1)
        last_id_step2 = msgs_before2[0].id if msgs_before2 else 0

        await client.send_message(bot_entity, f"/start {start_param}")

        # Wait for videos to arrive
        print(f"   ⏳ Waiting {WAIT_AFTER_DEEPLINK}s for videos...")
        
        # Poll periodically to see progress
        video_count = 0
        for wait_round in range(WAIT_AFTER_DEEPLINK // 5):
            await asyncio.sleep(5)
            temp_msgs = []
            async for msg in client.iter_messages(bot_entity, min_id=last_id_step2, limit=200):
                if msg.id > last_id_step2:
                    temp_msgs.append(msg)
            new_vids = sum(1 for m in temp_msgs if m.media and isinstance(m.media, MessageMediaDocument))
            if new_vids > video_count:
                video_count = new_vids
                print(f"      📥 {video_count} videos received so far...")
            elif new_vids > 0 and new_vids == video_count:
                # No new videos for 5 seconds, probably done
                print(f"      ✅ Looks like all {video_count} videos arrived!")
                break

        # Collect all video messages
        video_msgs = []
        async for msg in client.iter_messages(bot_entity, min_id=last_id_step2, limit=200):
            if msg.id > last_id_step2 and msg.media and isinstance(msg.media, MessageMediaDocument):
                video_msgs.append(msg)

        print(f"\n   📥 Total: {len(video_msgs)} video messages to download")

        # Download each video
        for msg in sorted(video_msgs, key=lambda m: m.id):
            caption = msg.text or msg.message or f"Episode_{msg.id}"
            ep_num = extract_episode_number(caption)
            safe_name = sanitize_filename(caption)

            # Skip if already downloaded
            if str(ep_num) in manifest and manifest[str(ep_num)].get("downloaded"):
                print(f"   ✅ Already have Ep {ep_num}, skipping")
                continue

            filename = f"{safe_name}.mp4"
            filepath = MEDIA_DIR / filename
            partpath = MEDIA_DIR / f"{filename}.part"

            # Expected size straight from Telegram — lets us tell a complete
            # file from a truncated one left by an interrupted run.
            try:
                expected = msg.file.size or 0
            except Exception:
                expected = 0

            # Skip only if the final file exists AND is complete.
            if filepath.exists() and (
                (expected and abs(filepath.stat().st_size - expected) <= 1024)
                or (not expected and filepath.stat().st_size > 1_000_000)
            ):
                print(f"   ✅ Already complete: {filename}, skipping")
                manifest[str(ep_num)] = {
                    "episode": ep_num,
                    "caption": caption,
                    "filename": filename,
                    "downloaded": True,
                    "message_id": msg.id,
                    "size_mb": round(filepath.stat().st_size / 1024 / 1024, 1),
                }
                manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
                continue

            print(f"   ⬇️  Downloading Ep {ep_num}: {filename}...")
            start_time = time.time()

            try:
                # Download to a .part file, then atomically rename on success.
                # If the process is killed mid-download, only the .part is left
                # (overwritten on the next attempt) — never a truncated .mp4.
                await client.download_media(
                    msg,
                    file=str(partpath),
                    progress_callback=lambda current, total: print(
                        f"\r      {current/1024/1024:.1f} / {total/1024/1024:.1f} MB "
                        f"({100*current/total:.0f}%)",
                        end="", flush=True,
                    ) if total else None,
                )
                actual = partpath.stat().st_size
                if expected and abs(actual - expected) > 1024:
                    raise IOError(f"incomplete: got {actual} of {expected} bytes")
                partpath.replace(filepath)
                elapsed = time.time() - start_time
                size_mb = filepath.stat().st_size / 1024 / 1024
                print(f"\n      ✅ Done! {size_mb:.1f} MB in {elapsed:.0f}s")

                manifest[str(ep_num)] = {
                    "episode": ep_num,
                    "caption": caption,
                    "filename": filename,
                    "downloaded": True,
                    "message_id": msg.id,
                    "size_mb": round(size_mb, 1),
                }

            except asyncio.CancelledError:
                # Killed mid-download — record progress and re-raise so the
                # process can actually exit (resume cleanly on the next run).
                print(f"\n      ⏸️  Cancelled while downloading Ep {ep_num}")
                manifest[str(ep_num)] = {
                    "episode": ep_num, "caption": caption, "filename": filename,
                    "downloaded": False, "message_id": msg.id, "error": "cancelled",
                }
                manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
                raise

            except Exception as e:
                print(f"\n      ❌ Failed: {e}")
                manifest[str(ep_num)] = {
                    "episode": ep_num,
                    "caption": caption,
                    "filename": filename,
                    "downloaded": False,
                    "message_id": msg.id,
                    "error": str(e),
                }

            # Save manifest after each download (crash-safe)
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

        # Delay between ranges
        print(f"\n   💤 Waiting {DELAY_BETWEEN_RANGES}s before next range...")
        await asyncio.sleep(DELAY_BETWEEN_RANGES)

    # Final summary
    print(f"\n{'='*60}")
    print("📊 DOWNLOAD SUMMARY")
    print(f"{'='*60}")
    total = len(manifest)
    downloaded = sum(1 for v in manifest.values() if isinstance(v, dict) and v.get("downloaded"))
    failed = total - downloaded
    print(f"   Total episodes: {total}")
    print(f"   Downloaded:     {downloaded}")
    print(f"   Failed:         {failed}")

    if failed:
        print("\n   Failed episodes:")
        for k, v in manifest.items():
            if isinstance(v, dict) and not v.get("downloaded"):
                print(f"      Ep {k}: {v.get('error', 'unknown')}")

    total_size = sum(
        v.get("size_mb", 0) for v in manifest.values() if isinstance(v, dict)
    )
    print(f"\n   Total size: {total_size:.1f} MB ({total_size/1024:.1f} GB)")

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"\n💾 Manifest saved to manifest.json")

    await client.disconnect()
    print("👋 Disconnected from Telegram")


def main():
    parser = argparse.ArgumentParser(description="Download videos from reference bot")
    parser.add_argument("--dry-run", action="store_true",
                        help="Just show buttons, don't download")
    parser.add_argument("--range", type=int, default=None,
                        help="Only process button at this row index")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run, only_range=args.range))


if __name__ == "__main__":
    main()
