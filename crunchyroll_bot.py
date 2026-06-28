import argparse
import asyncio
import os
import time
from pathlib import Path

import yt_dlp
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeVideo

load_dotenv()

# Telegram Configuration
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_FILE = "downloader_session"
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "./media")).expanduser().resolve()

# Crunchyroll Configuration
CR_USERNAME = "koshanlay1994@gmail.com"
CR_PASSWORD = "13377331@SAS"

# Default upload destination: 'me' sends it to your Saved Messages.
# You can change this to a channel username (e.g., '@my_anime_channel') or ID
DESTINATION = "me"


def download_crunchyroll(url: str) -> str:
    """Download video from Crunchyroll using yt-dlp."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    
    print(f"📡 Initializing yt-dlp for Crunchyroll...")
    ydl_opts = {
        'username': CR_USERNAME,
        'password': CR_PASSWORD,
        
        # Try to pull cookies from safari if username/password hits a cloudflare block
        'cookiesfrombrowser': ('safari',), 
        
        # Best video (max 1080p) + Best audio
        'format': 'bestvideo[height<=1080]+bestaudio/best',
        
        # Subtitles configuration (embed English subs)
        'writesubtitles': True,
        'subtitleslangs': ['en', 'en-US'],
        'embedsubtitles': True,
        
        # Merge into MKV format to properly support multiple tracks and subtitles
        'merge_output_format': 'mkv',
        
        # Save path template
        'outtmpl': str(MEDIA_DIR / '%(series)s - %(episode)s.%(ext)s'),
        
        'quiet': False,
        'no_warnings': False
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        print(f"⬇️ Starting download for: {url}")
        info = ydl.extract_info(url, download=True)
        
        # Determine the final filename after merging
        filename = ydl.prepare_filename(info)
        filepath = Path(filename)
        
        # yt-dlp changes the extension to .mkv if it merged streams
        mkv_path = filepath.with_suffix('.mkv')
        if mkv_path.exists():
            filepath = mkv_path
        else:
            mp4_path = filepath.with_suffix('.mp4')
            if mp4_path.exists():
                filepath = mp4_path
                
        if filepath.exists():
            print(f"✅ Download complete: {filepath.name} ({filepath.stat().st_size / 1024 / 1024:.1f} MB)")
            return str(filepath)
        else:
            raise FileNotFoundError(f"Could not find the downloaded file at {filepath}")


async def upload_to_telegram(filepath: str):
    """Upload the downloaded file to Telegram."""
    path = Path(filepath)
    print(f"\n🚀 Connecting to Telegram to upload to '{DESTINATION}'...")
    
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()

    me = await client.get_me()
    print(f"✅ Logged in as User: {me.first_name}")

    start_time = time.time()
    
    try:
        # Progress callback for the terminal
        async def progress(current, total):
            if total > 0:
                print(f"\r   ⬆️ Uploading: {current/1024/1024:.1f} / {total/1024/1024:.1f} MB ({100*current/total:.0f}%)", end="", flush=True)

        print(f"📤 Sending file...")
        await client.send_file(
            DESTINATION,
            str(path),
            caption=f"🎥 **{path.stem}**\n\nDownloaded via Automated Pipeline",
            progress_callback=progress,
            attributes=[
                DocumentAttributeVideo(
                    duration=0, # Let Telegram auto-detect
                    w=1920,
                    h=1080,
                    supports_streaming=True
                )
            ]
        )
        elapsed = time.time() - start_time
        print(f"\n✅ Upload successful! (Took {elapsed:.1f}s)")
        
        # Auto-cleanup to save server disk space
        print(f"🧹 Deleting local file to save space...")
        path.unlink(missing_ok=True)
        print("✅ Cleanup complete.")
        
    except Exception as e:
        print(f"\n❌ Upload failed: {e}")
    finally:
        await client.disconnect()


async def main(url: str):
    print(f"{'='*60}")
    print(f"🌊 CRUNCHYROLL TO TELEGRAM PIPELINE")
    print(f"{'='*60}")
    
    try:
        # Step 1: Download
        filepath = download_crunchyroll(url)
        
        # Step 2: Upload
        if filepath:
            await upload_to_telegram(filepath)
            
    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}")
        
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download from Crunchyroll and upload to Telegram")
    parser.add_argument("url", type=str, help="The Crunchyroll Episode URL")
    args = parser.parse_args()
    
    asyncio.run(main(args.url))
