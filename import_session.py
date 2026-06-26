"""Import an existing Telegram session into the multi-tenant dashboard.

Takes a Telethon `.session` file (or a StringSession) that is ALREADY logged in,
verifies it's authorized, copies it in as a managed account, and registers it so
the operator console can see and control it — no phone/OTP needed.

Usage (run on the server, inside /opt/videobot with the venv):

    .venv/bin/python import_session.py downloader_session.session
    .venv/bin/python import_session.py /path/to/account.session --name "Sales acct"
    .venv/bin/python import_session.py --string "1Aab...==" --name "From string"

Then restart the web app so it loads the new account:
    sudo systemctl restart videobot-web
"""
import argparse
import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession, SQLiteSession
from telethon.utils import get_display_name

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSIONS_DIR = Path(os.getenv("SESSIONS_DIR", "sessions")).expanduser().resolve()
REGISTRY = SESSIONS_DIR / "registry.json"


def _register(sid: str, me) -> dict:
    reg = {}
    if REGISTRY.exists():
        try:
            reg = json.loads(REGISTRY.read_text())
        except Exception:
            reg = {}
    info = {
        "user_id": me.id,
        "name": get_display_name(me) or me.username or str(me.id),
        "username": me.username,
        "phone": me.phone,
        "created": time.time(),
        "last_seen": time.time(),
    }
    reg[sid] = info
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=2))
    return info


async def import_file(path: str, name: str | None):
    # Resolve the source .session file (with or without the extension).
    p = Path(path)
    if not p.exists() and Path(str(p) + ".session").exists():
        p = Path(str(p) + ".session")
    if not p.exists():
        print(f"❌ Session file not found: {path}")
        return None
    client_name = str(p)[:-8] if str(p).endswith(".session") else str(p)

    client = TelegramClient(client_name, API_ID, API_HASH)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print(f"❌ {p.name} is not an authorized session (cannot import).")
            return None
        me = await client.get_me()
    finally:
        await client.disconnect()

    sid = uuid.uuid4().hex
    dest = SESSIONS_DIR / f"{sid}.session"
    shutil.copyfile(p, dest)
    return sid, me


async def import_string(s: str, name: str | None):
    client = TelegramClient(StringSession(s), API_ID, API_HASH)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print("❌ The provided session string is not authorized.")
            return None
        me = await client.get_me()
    finally:
        await client.disconnect()

    sid = uuid.uuid4().hex
    dest = SESSIONS_DIR / f"{sid}.session"
    ss = StringSession(s)
    sq = SQLiteSession(str(dest)[:-8])  # SQLiteSession appends ".session"
    sq.set_dc(ss._dc_id, ss._server_address, ss._port)
    sq.auth_key = ss._auth_key
    sq.save()
    return sid, me


async def main():
    ap = argparse.ArgumentParser(description="Import an existing Telegram session into the dashboard.")
    ap.add_argument("session", nargs="?", help="path to an existing .session file")
    ap.add_argument("--string", help="import from a Telethon StringSession instead of a file")
    ap.add_argument("--name", help="optional display label override")
    args = ap.parse_args()

    if not API_ID or not API_HASH:
        print("❌ TELEGRAM_API_ID / TELEGRAM_API_HASH missing in .env")
        return
    if not args.session and not args.string:
        ap.error("provide a .session path or --string")

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    result = await (import_string(args.string, args.name) if args.string
                    else import_file(args.session, args.name))
    if not result:
        return
    sid, me = result
    info = _register(sid, me)
    if args.name:
        reg = json.loads(REGISTRY.read_text())
        reg[sid]["name"] = args.name
        REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=2))
        info["name"] = args.name

    print("✅ Imported session as a managed account:")
    print(f"     name:     {info['name']}")
    print(f"     username: @{info['username']}" if info["username"] else "     username: (none)")
    print(f"     phone:    {info['phone']}")
    print(f"     sid:      {sid}")
    print(f"     file:     {SESSIONS_DIR / (sid + '.session')}")
    print("\n👉 Restart the web app so the operator console picks it up:")
    print("     sudo systemctl restart videobot-web")


if __name__ == "__main__":
    asyncio.run(main())
