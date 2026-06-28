import argparse
import asyncio
import os
import re
import time
import urllib.parse
from pathlib import Path
import xml.etree.ElementTree as ET

import requests
import yt_dlp
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeVideo
from torrentp import TorrentDownloader

load_dotenv()

# Telegram Configuration
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_FILE = "downloader_session"
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "./media")).expanduser().resolve()
DESTINATION = "me"


def search_nyaa(query: str) -> dict | None:
    """Search Nyaa.si RSS feed for the best match."""
    print(f"🔍 Searching Nyaa.si for: '{query}'")
    
    # URL encode the query
    encoded_query = urllib.parse.quote(query)
    # c=1_2 is English translated anime, f=0 is no filter
    rss_url = f"https://nyaa.si/?page=rss&q={encoded_query}&c=1_2&f=0"
    
    try:
        response = requests.get(rss_url, timeout=10)
        response.raise_for_status()
        
        root = ET.fromstring(response.text)
        items = root.findall('./channel/item')
        
        if not items:
            return None
            
        # Get the first (most seeded/relevant based on default sort) item
        best_item = items[0]
        
        title = best_item.find('title').text
        link = best_item.find('link').text  # The .torrent download link
        
        # Try to find magnet link in nyaa namespace if needed, but .torrent link works for torrentp
        return {
            "title": title,
            "link": link
        }
    except Exception as e:
        print(f"❌ Error searching Nyaa: {e}")
        return None

async def download_torrent(link: str, title: str) -> str | None:
    """Download a torrent file using torrentp."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"⬇️ Starting torrent download: {title}")
    
    try:
        # We need a unique subfolder so we can find exactly what was downloaded
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
        download_path = MEDIA_DIR / safe_title
        download_path.mkdir(exist_ok=True)
        
        # Initialize torrent downloader
        # torrentp handles both magnet links and .torrent URLs
        torrent = TorrentDownloader(link, str(download_path))
        
        # Start download (it runs until completion)
        # torrentp has an async API in recent versions
        if asyncio.iscoroutinefunction(torrent.start_download):
            await torrent.start_download()
        else:
            # Run sync function in executor if it's an older sync version
            await asyncio.to_thread(torrent.start_download)
            
        print(f"✅ Torrent download complete!")
        
        # Find the video file (.mkv or .mp4) in the download directory
        video_files = list(download_path.rglob("*.mkv")) + list(download_path.rglob("*.mp4"))
        
        if not video_files:
            print("❌ Could not find a video file in the downloaded torrent.")
            return None
            
        # Return the largest video file found
        video_files.sort(key=lambda x: x.stat().st_size, reverse=True)
        return str(video_files[0])
        
    except Exception as e:
        print(f"❌ Torrent download failed: {e}")
        return None

def download_ytdlp(url: str) -> str | None:
    """Fallback: Download using yt-dlp for non-DRM sites."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"📡 Attempting yt-dlp download for URL: {url}")
    
    ydl_opts = {
        'format': 'bestvideo[height<=1080]+bestaudio/best',
        'merge_output_format': 'mkv',
        'outtmpl': str(MEDIA_DIR / '%(title)s.%(ext)s'),
        'quiet': False,
        'no_warnings': True,
        'cookiesfrombrowser': ('safari',) # try to use safari cookies for auth
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            filepath = Path(filename)
            
            # Handle possible extension change due to merge
            mkv_path = filepath.with_suffix('.mkv')
            if mkv_path.exists():
                filepath = mkv_path
            
            if filepath.exists():
                print(f"✅ yt-dlp download complete: {filepath.name}")
                return str(filepath)
    except Exception as e:
        print(f"❌ yt-dlp download failed: {e}")
        
    return None

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
                    duration=0,
                    w=1920,
                    h=1080,
                    supports_streaming=True
                )
            ]
        )
        elapsed = time.time() - start_time
        print(f"\n✅ Upload successful! (Took {elapsed:.1f}s)")
        
        print(f"🧹 Deleting local file to save space...")
        path.unlink(missing_ok=True)
        # Also try to remove parent directory if it's empty (for torrents)
        try:
            path.parent.rmdir()
        except OSError:
            pass
        print("✅ Cleanup complete.")
        
    except Exception as e:
        print(f"\n❌ Upload failed: {e}")
    finally:
        await client.disconnect()

async def main(query: str):
    print(f"{'='*60}")
    print(f"🌊 ANIME DOWNLODER PIPELINE (NYAA + FALLBACK)")
    print(f"{'='*60}")
    
    filepath = None
    
    # Determine if it's a URL or a search query
    if query.startswith("http://") or query.startswith("https://"):
        print("🔗 Input is a URL. Attempting direct download with yt-dlp...")
        filepath = download_ytdlp(query)
    else:
        print("🔎 Input is a search query. Attempting Nyaa.si torrent download...")
        result = search_nyaa(query)
        if result:
            print(f"✅ Found release: {result['title']}")
            filepath = await download_torrent(result['link'], result['title'])
        else:
            print("❌ No results found on Nyaa.")
            
    # Upload if successful
    if filepath and Path(filepath).exists():
        await upload_to_telegram(filepath)
    else:
        print("\n❌ Pipeline failed: No file was downloaded.")
        
    print(f"{'='*60}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Anime via Nyaa or yt-dlp and upload to Telegram")
    parser.add_argument("query", type=str, help="Search query (e.g. 'One Piece 1089 SubsPlease 1080p') or a URL")
    args = parser.parse_args()
    
    asyncio.run(main(args.query))
