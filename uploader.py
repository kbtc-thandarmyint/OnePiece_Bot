"""Upload downloaded videos to a private Telegram channel for forwarding.

After downloading episodes with downloader.py, this script:
1.  Creates or uses an existing private channel
2.  Uploads each video with its episode caption
3.  Saves channel_manifest.json mapping episode → channel message_id

Usage:
    python uploader.py                          # upload all from manifest
    python uploader.py --channel-id -100XXXX    # use existing channel
    python uploader.py --dry-run                # just show what would be uploaded
"""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "./media")).expanduser().resolve()
SESSION_FILE = "downloader_session"  # reuse the same session

CHANNEL_MANIFEST = Path("channel_manifest.json")
MANIFEST = Path("manifest.json")

# Delay between uploads to avoid rate limits
UPLOAD_DELAY = 5  # seconds


async def create_channel(client: TelegramClient) -> int:
    """Create a private channel for video storage."""
    print("📺 Creating private channel: 'One Piece Video Storage'...")
    result = await client(CreateChannelRequest(
        title="One Piece Video Storage",
        about="Private storage for One Piece episodes - used by video bot",
        megagroup=False,  # False = channel, True = supergroup
    ))
    channel = result.chats[0]
    channel_id = -100 * 0 + channel.id  # Convert to full ID format
    # For Telethon, we need the marked ID
    from telethon.utils import get_peer_id
    full_id = get_peer_id(channel)
    print(f"   ✅ Channel created! ID: {full_id}")
    return full_id


async def run(channel_id: int | None = None, dry_run: bool = False):
    if not MANIFEST.exists():
        print("❌ manifest.json not found. Run downloader.py first!")
        return

    manifest = json.loads(MANIFEST.read_text())
    print(f"📋 Loaded manifest with {len(manifest)} episodes")

    # Filter to downloaded episodes only
    episodes = {
        k: v for k, v in manifest.items()
        if v.get("downloaded")
    }
    print(f"   {len(episodes)} episodes ready for upload")

    if not episodes:
        print("❌ No downloaded episodes found!")
        return

    # Load existing channel manifest if resuming
    channel_manifest = {}
    if CHANNEL_MANIFEST.exists():
        channel_manifest = json.loads(CHANNEL_MANIFEST.read_text())
        print(f"   {len(channel_manifest)} already uploaded to channel")

    if dry_run:
        print("\n🏁 Dry run — would upload these episodes:")
        for ep_num in sorted(episodes.keys(), key=int):
            ep = episodes[ep_num]
            status = "✅ already uploaded" if ep_num in channel_manifest else "📤 pending"
            print(f"   Ep {ep_num}: {ep['filename']} ({ep.get('size_mb', '?')} MB) — {status}")
        return

    # Connect
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()
    me = await client.get_me()
    print(f"\n✅ Logged in as {me.first_name}")

    # Create or use channel
    if channel_id:
        print(f"📺 Using provided channel ID: {channel_id}")
    elif channel_manifest.get("_channel_id"):
        channel_id = channel_manifest["_channel_id"]
        print(f"📺 Resuming with channel ID: {channel_id}")
    else:
        channel_id = await create_channel(client)

    # Save channel ID in manifest
    channel_manifest["_channel_id"] = channel_id

    # Get channel entity
    try:
        channel_entity = await client.get_entity(channel_id)
        print(f"   Channel: {channel_entity.title}")
    except Exception as e:
        print(f"❌ Could not access channel {channel_id}: {e}")
        print("   Make sure the bot is added as admin, or create a new channel")
        await client.disconnect()
        return

    # Upload episodes in order
    sorted_episodes = sorted(episodes.items(), key=lambda x: int(x[0]))

    for ep_num, ep_data in sorted_episodes:
        if ep_num in channel_manifest and ep_num != "_channel_id":
            print(f"   ✅ Ep {ep_num} already uploaded (msg_id: {channel_manifest[ep_num]['message_id']})")
            continue

        filepath = MEDIA_DIR / ep_data["filename"]
        if not filepath.exists():
            print(f"   ❌ Ep {ep_num}: file not found at {filepath}")
            continue

        caption = ep_data.get("caption", f"One Piece Ep-{ep_num}")
        size_mb = ep_data.get("size_mb", filepath.stat().st_size / 1024 / 1024)

        print(f"\n   📤 Uploading Ep {ep_num}: {ep_data['filename']} ({size_mb:.1f} MB)...")
        start_time = time.time()

        try:
            msg = await client.send_file(
                channel_entity,
                filepath,
                caption=caption,
                supports_streaming=True,
                progress_callback=lambda current, total: print(
                    f"\r      {current/1024/1024:.1f} / {total/1024/1024:.1f} MB "
                    f"({100*current/total:.0f}%)",
                    end="", flush=True,
                ) if total else None,
            )
            elapsed = time.time() - start_time
            print(f"\n      ✅ Uploaded! msg_id: {msg.id} ({elapsed:.0f}s)")

            channel_manifest[ep_num] = {
                "episode": int(ep_num),
                "caption": caption,
                "message_id": msg.id,
                "filename": ep_data["filename"],
            }

        except Exception as e:
            print(f"\n      ❌ Failed: {e}")
            continue

        # Save after each upload (crash-safe)
        CHANNEL_MANIFEST.write_text(
            json.dumps(channel_manifest, indent=2, ensure_ascii=False)
        )

        # Delay between uploads
        if UPLOAD_DELAY:
            print(f"      💤 Waiting {UPLOAD_DELAY}s...")
            await asyncio.sleep(UPLOAD_DELAY)

    # Final summary
    uploaded = len([k for k in channel_manifest if k != "_channel_id"])
    print(f"\n{'='*60}")
    print(f"📊 UPLOAD SUMMARY")
    print(f"{'='*60}")
    print(f"   Channel ID: {channel_id}")
    print(f"   Episodes uploaded: {uploaded}/{len(episodes)}")
    print(f"   Manifest saved to: {CHANNEL_MANIFEST}")

    print(f"\n💡 Add this to your .env:")
    print(f"   STORAGE_CHANNEL_ID={channel_id}")

    await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Upload videos to private channel")
    parser.add_argument("--channel-id", type=int, default=None,
                        help="Existing channel ID to use")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be uploaded without uploading")
    args = parser.parse_args()
    asyncio.run(run(channel_id=args.channel_id, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
