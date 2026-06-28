import argparse
import asyncio
import os
import re
import subprocess
import time
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeVideo
from dotenv import load_dotenv
from openai import OpenAI
import json

load_dotenv()

# Telegram Configuration
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_FILE = "downloader_session"
DESTINATION = "me"

# DeepSeek Setup
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
llm_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

SYSTEM_PROMPT = """You are an expert anime translator translating One Piece subtitles into Burmese.
Your translation MUST use modern Gen Z slang, keep it funny, cool, and full of 'rizz'. 
Keep all character names (Luffy, Zoro, etc.) and terms (Haki, Devil Fruit) in English.
You will receive a JSON array of subtitle lines. 
CRITICAL: You MUST preserve all ASS formatting tags (e.g. {\\fad...}) EXACTLY as they are.
Return ONLY a valid JSON array of strings of the exact same length, containing the translations."""


def translate_batch_llm(texts):
    if not texts: return []
    try:
        req = json.dumps({"texts": texts}, ensure_ascii=False)
        response = llm_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": req}
            ]
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```json"):
            content = content[7:-3].strip()
        elif content.startswith("```"):
            content = content[3:-3].strip()
        
        result = json.loads(content)
        # Handle if LLM returns a list directly or a dict
        if isinstance(result, dict) and "texts" in result:
            translated = result["texts"]
        elif isinstance(result, list):
            translated = result
        else:
            translated = list(result.values())[0]
            
        if len(translated) != len(texts):
            print(f"⚠️ Mismatch length: {len(translated)} vs {len(texts)}")
            return texts
        return translated
    except Exception as e:
        print(f"⚠️ DeepSeek LLM Error: {e}")
        return texts

def translate_ass_file(input_ass: str, output_ass: str):
    print("🌍 Starting DeepSeek Gen Z translation to Myanmar...")
    start_time = time.time()
    
    with open(input_ass, 'r', encoding='utf-8') as f_in:
        lines = f_in.readlines()
        
    out_lines = []
    dialogue_indices = []
    dialogue_texts = []
    
    for i, line in enumerate(lines):
        if line.startswith("Dialogue:"):
            parts = line.split(',', 9)
            if len(parts) == 10:
                dialogue_indices.append(i)
                dialogue_texts.append(parts[9].strip())
            out_lines.append(line)
        elif line.startswith("Style:"):
            parts = line.split(',', 2)
            if len(parts) >= 3:
                out_lines.append(f"{parts[0]},PDA18-Stone,{parts[2]}")
            else:
                out_lines.append(line)
        else:
            out_lines.append(line)
            
    # Batch translate in chunks of 20
    chunk_size = 20
    print(f"   Translating {len(dialogue_texts)} lines in batches of {chunk_size}...")
    
    for i in range(0, len(dialogue_texts), chunk_size):
        chunk = dialogue_texts[i:i+chunk_size]
        print(f"   -> Translating batch {i//chunk_size + 1}/{(len(dialogue_texts)+chunk_size-1)//chunk_size}...")
        translated_chunk = translate_batch_llm(chunk)
        
        for j, text in enumerate(translated_chunk):
            idx = dialogue_indices[i+j]
            orig_line = lines[idx]
            parts = orig_line.split(',', 9)
            metadata = ",".join(parts[:9]) + ","
            out_lines[idx] = metadata + text + "\n"
            
    with open(output_ass, 'w', encoding='utf-8') as f_out:
        f_out.writelines(out_lines)
                
    elapsed = time.time() - start_time
    print(f"✅ DeepSeek Translation complete! (Took {elapsed:.1f}s)")


def extract_subtitles(mkv_path: str, output_ass: str):
    print("🎬 Extracting original subtitles from MKV...")
    cmd = [
        "ffmpeg", "-y", "-v", "error", 
        "-i", mkv_path, 
        "-map", "0:s:0", # Map the first subtitle stream
        output_ass
    ]
    subprocess.run(cmd, check=True)
    print("✅ Subtitles extracted.")


def hardcode_subtitles(mkv_path: str, ass_path: str, mp4_path: str):
    print("🔥 Burning translated subtitles into video using Hardware Acceleration with Padauk font...")
    
    m_path = Path(mkv_path)
    work_dir = m_path.parent
    fonts_dir = Path("fonts").resolve()
    
    cmd = [
        "ffmpeg", "-y", 
        "-i", m_path.name,
        "-vf", f"subtitles={Path(ass_path).name}:fontsdir={str(fonts_dir)}",
        # Apple Silicon hardware encoder for beautiful rendering
        "-c:v", "h264_videotoolbox", "-b:v", "10M",
        "-c:a", "copy",
        Path(mp4_path).name
    ]
    
    start_time = time.time()
    subprocess.run(cmd, check=True, cwd=str(work_dir))
    elapsed = time.time() - start_time
    print(f"✅ Hardware Encoding complete! (Took {elapsed:.1f}s)")


async def upload_to_telegram(filepath: str):
    print(f"🚀 Uploading {Path(filepath).name} to Telegram...")
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()

    start_time = time.time()
    try:
        async def progress(current, total):
            if total > 0:
                print(f"\r   ⬆️ Uploading: {current/1024/1024:.1f} / {total/1024/1024:.1f} MB", end="", flush=True)

        await client.send_file(
            DESTINATION,
            filepath,
            caption=f"🎥 **{Path(filepath).stem}**\n\n🇲🇲 Translated to Myanmar",
            progress_callback=progress,
            attributes=[DocumentAttributeVideo(duration=0, w=1920, h=1080, supports_streaming=True)]
        )
        print(f"\n✅ Upload successful! (Took {time.time() - start_time:.1f}s)")
    finally:
        await client.disconnect()


async def main(mkv_file: str):
    mkv_path = Path(mkv_file).resolve()
    if not mkv_path.exists():
        print(f"❌ File not found: {mkv_path}")
        return

    work_dir = mkv_path.parent
    original_ass = work_dir / "original.ass"
    myanmar_ass = work_dir / "myanmar.ass"
    final_mp4 = work_dir / f"{mkv_path.stem}_Myanmar.mp4"

    print(f"{'='*60}")
    print(f"🇲🇲 AUTOMATED MYANMAR TRANSLATION PIPELINE (PDA-18 FONT)")
    print(f"{'='*60}")

    try:
        extract_subtitles(str(mkv_path), str(original_ass))
        translate_ass_file(str(original_ass), str(myanmar_ass))
        hardcode_subtitles(str(mkv_path), str(myanmar_ass), str(final_mp4))
        
        await upload_to_telegram(str(final_mp4))
        
        print("\n🧹 Cleaning up temporary files...")
        original_ass.unlink(missing_ok=True)
        myanmar_ass.unlink(missing_ok=True)
        # We don't delete final_mp4 during testing, but you can add it here.
        
    except Exception as e:
        print(f"\n❌ Pipeline Error: {e}")
        
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Translate and Hardcode Subtitles")
    parser.add_argument("mkv_file", type=str, help="Path to the original MKV file")
    args = parser.parse_args()
    
    asyncio.run(main(args.mkv_file))
