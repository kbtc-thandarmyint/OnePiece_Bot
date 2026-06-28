import sys
import asyncio
from telethon import TelegramClient
import os
from dotenv import load_dotenv

load_dotenv()
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_FILE = "downloader_session"

phone = "+66620033365"
client = TelegramClient(SESSION_FILE, API_ID, API_HASH)

async def main():
    await client.connect()
    if not await client.is_user_authorized():
        if len(sys.argv) == 1:
            # Step 1: Request code
            res = await client.send_code_request(phone)
            # Save the phone_code_hash so sign_in works in a separate run
            with open("phone_code_hash.txt", "w") as f:
                f.write(res.phone_code_hash)
            print("CODE_SENT")
        else:
            # Step 2: Sign in with code
            code = sys.argv[1]
            with open("phone_code_hash.txt", "r") as f:
                phone_code_hash = f.read().strip()
            try:
                await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
                print("LOGGED_IN")
            except Exception as e:
                print(f"FAILED: {e}")
    else:
        print("ALREADY_LOGGED_IN")
    await client.disconnect()

asyncio.run(main())
