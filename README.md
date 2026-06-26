# 🏴‍☠️ OnePiece Bot — Multi-Service Telegram Platform

A modular Telegram platform with **three independent services** running on a single server:

| Service | Description | Entry Point |
|---------|-------------|-------------|
| **Video Bot** | One Piece MMSub episode delivery via Telegram forwarding | `bot.py` |
| **Web Manager** | Multi-tenant Telegram session manager + operator console | `web_app.py` |
| **Top-up Bot** | MLBB Diamond & Telegram Stars customer-facing top-up bot | `topup_bot.py` |

Plus CLI tools for downloading and uploading video content:

| Tool | Description | Entry Point |
|------|-------------|-------------|
| **Downloader** | Automated episode scraper from a reference bot | `downloader.py` |
| **Uploader** | Uploads downloaded episodes to a private storage channel | `uploader.py` |
| **Session Import** | Import existing Telegram sessions into the web manager | `import_session.py` |
| **Debug Bot** | Interactive debugger for reference bot analysis | `debug_bot.py` |

---

## 📁 Project Structure

```
OnePiece_Bot/
├── bot.py                  # Telegram video-forwarding bot (range-based menu)
├── config.py               # Centralised config loader (reads .env via python-dotenv)
├── library.py              # Episode manifest manager + range grouping helpers
├── downloader.py           # CLI: scrape episodes from source bot via Telethon
├── uploader.py             # CLI: upload episodes to private channel for forwarding
├── topup_bot.py            # MLBB Diamond + Telegram Stars top-up bot
├── web_app.py              # FastAPI multi-tenant Telegram web manager + admin console
├── import_session.py       # CLI: import existing .session files into the dashboard
├── debug_bot.py            # CLI: debug tool for inspecting source bot responses
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template (copy to .env)
├── .gitignore              # Git ignore rules
│
├── web/                    # Frontend SPA (served by FastAPI)
│   ├── index.html          # Main HTML (login + user view + operator console)
│   ├── app.js              # Client-side JavaScript (all UI logic)
│   ├── style.css           # Complete CSS (glassmorphism + dark theme)
│   ├── favicon.png         # Browser icon
│   ├── og-image.png        # Open Graph social preview image
│   └── mlbb-logo.png       # MLBB branding asset
│
├── deploy/                 # Production deployment configs
│   ├── push_to_prod.sh     # One-command deploy script (rsync + systemd)
│   ├── nginx_videobot.conf # Nginx reverse proxy (HTTPS + WebSocket)
│   ├── videobot-web.service    # systemd unit: web manager
│   ├── videobot.service        # systemd unit: video bot
│   ├── topup-bot.service       # systemd unit: top-up bot
│   └── .deploy.env         # Local deploy secrets (gitignored)
│
├── media/                  # Downloaded video files (gitignored)
│   └── .gitkeep
│
└── sessions/               # Per-user Telegram session files (gitignored)
```

---

## 🚀 Quick Start (Local Development)

### Prerequisites

- **Python 3.11+**
- **Telegram Bot Token** — get from [@BotFather](https://t.me/BotFather)
- **Telegram API credentials** — get from [my.telegram.org](https://my.telegram.org)

### 1. Clone & Install

```bash
git clone https://github.com/kbtc-thandarmyint/OnePiece_Bot.git
cd OnePiece_Bot

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and fill in your credentials:

| Variable | Required For | Description |
|----------|-------------|-------------|
| `BOT_TOKEN` | Video Bot | BotFather token for the episode bot |
| `TELEGRAM_API_ID` | All | API ID from my.telegram.org |
| `TELEGRAM_API_HASH` | All | API hash from my.telegram.org |
| `STORAGE_CHANNEL_ID` | Video Bot | Private channel ID (set after running uploader) |
| `TELEGRAM_TOPUP_BOT_TOKEN` | Top-up Bot | Separate BotFather token for top-up bot |
| `MLBB_HUB_URL` | Top-up Bot | DK Gaming supplier hub API URL |
| `MLBB_HUB_KEY` | Top-up Bot | Supplier hub API key |
| `ADMIN_USERNAME` | Web Manager | Operator console username |
| `ADMIN_PASSWORD` | Web Manager | Operator console password |

### 3. Run Services

**Video Bot** (episode forwarding):
```bash
python bot.py
```

**Web Manager** (multi-tenant dashboard):
```bash
python web_app.py
# → http://localhost:8080
```

**Top-up Bot** (MLBB diamond + Stars):
```bash
python topup_bot.py
```

---

## ⚙️ Service Details

### 🏴‍☠️ Video Bot (`bot.py`)

Replicates the behavior of `@OnePiece_MMSub_s1_bot`:

- **Range-based episode menu** — buttons like `(1-25)`, `(26-50)`, etc.
- **Channel forwarding** — forwards videos from a private storage channel (no 50MB upload limit)
- **Myanmar language labels** — button text in Burmese
- **Access control** — optional whitelist via `ALLOWED_USER_IDS`
- **Commands**: `/start`, `/list`, `/reload`

**Workflow**:
1. Run `downloader.py` to scrape episodes from the source bot
2. Run `uploader.py` to upload episodes to your private channel
3. Set `STORAGE_CHANNEL_ID` in `.env`
4. Run `bot.py` to serve episodes to users

### 🌐 Web Manager (`web_app.py`)

A FastAPI-based multi-tenant Telegram session manager:

- **User view** (`/`) — login with phone + OTP, browse chats, send messages, view media
- **Operator console** (`/operator`) — oversee all logged-in accounts, read any chat, send as any account
- **Multi-tenant isolation** — each browser gets its own `vb_session` cookie → isolated Telethon client
- **MLBB integration** — player name lookup via DK Gaming supplier hub
- **Session export** — download `.session` files or StringSession for use elsewhere
- **Live updates** — WebSocket real-time message streaming
- **Rate limiting** — per-IP rate limits on OTP and MLBB lookup endpoints
- **Auto-cleanup** — idle sessions disconnected after `IDLE_TIMEOUT` (default: 30 min)

**API Endpoints**:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/auth/status` | Check login state |
| POST | `/api/auth/send_code` | Send OTP code |
| POST | `/api/auth/sign_in` | Submit OTP code |
| POST | `/api/auth/password` | Submit 2FA password |
| POST | `/api/auth/logout` | Log out + delete session |
| GET | `/api/chats` | List user's Telegram chats |
| GET | `/api/chats/{id}/messages` | Get chat messages |
| POST | `/api/chats/{id}/send` | Send a message |
| POST | `/api/mlbb/check` | Validate MLBB player ID |
| GET | `/api/admin/sessions` | List all accounts (admin) |
| WS | `/ws` | Real-time message updates |

### 💎 Top-up Bot (`topup_bot.py`)

Customer-facing Telegram bot for MLBB diamond and Telegram Stars top-ups:

- **Player validation** — checks MLBB ID + server against the DK shop supplier hub
- **Saved players** — remembers up to 10 MLBB IDs per user
- **Diamond packages** — fetches live package list from the hub
- **Telegram Stars** — one-click 50 Stars claim
- **Order placement** — places real orders through the supplier hub (gated behind `TOPUP_ORDERS_ENABLED`)
- **Claim limits** — per-user one-time claim + global daily/total caps
- **Admin bypass** — `TOPUP_ADMIN_IDS` can place unlimited orders
- **Login required** — checks if user has an active web session before proceeding

### 🔧 CLI Tools

**Downloader** (`downloader.py`):
```bash
python downloader.py                # Full download
python downloader.py --dry-run      # Just show buttons
python downloader.py --range 2      # Only process row index 2
```
- Automates the two-step deep-link flow of the source bot
- Crash-safe manifest tracking (saves after each download)
- Atomic downloads (`.part` → `.mp4` rename on completion)

**Uploader** (`uploader.py`):
```bash
python uploader.py                          # Upload all from manifest
python uploader.py --channel-id -100XXXX    # Use existing channel
python uploader.py --dry-run                # Show what would be uploaded
```
- Creates a private channel or uses an existing one
- Crash-safe channel manifest (saves after each upload)
- Rate-limited with configurable delay between uploads

**Session Import** (`import_session.py`):
```bash
python import_session.py downloader_session.session
python import_session.py --string "1Aab...=="
python import_session.py /path/to/account.session --name "Sales acct"
```
- Import existing `.session` files into the web dashboard
- Supports both file-based and StringSession imports

---

## 🖥️ Server Deployment

### Server Requirements

- **Ubuntu 20.04+** (or any systemd-based Linux)
- **Python 3.11+**
- **Nginx** (reverse proxy with HTTPS)
- **Certbot** (Let's Encrypt SSL certificates)
- **A domain** pointed to your server IP

### Step 1: Provision the Server

```bash
# SSH into your server
ssh thomas@your-server-ip

# Install system dependencies
sudo apt update && sudo apt install -y python3.11 python3.11-venv nginx certbot python3-certbot-nginx

# Create application directory
sudo mkdir -p /opt/videobot
sudo chown thomas:thomas /opt/videobot
cd /opt/videobot

# Create virtual environment
python3.11 -m venv .venv
```

### Step 2: Upload Code

**Option A — Using the deploy script** (from your local machine):

```bash
# Create deploy/.deploy.env with your secrets:
cat > deploy/.deploy.env << 'EOF'
SSHPASS=your-ssh-password
ADMIN_PASSWORD=your-admin-password
MLBB_HUB_KEY=your-hub-key
MLBB_HUB_URL=https://root.dkgamingshop.com/api
TELEGRAM_TOPUP_BOT_TOKEN=your-topup-bot-token
TOPUP_ORDERS_ENABLED=false
TOPUP_ADMIN_IDS=your-telegram-user-id
EOF

# Run the one-command deploy
./deploy/push_to_prod.sh
```

**Option B — Manual upload**:

```bash
# From your local machine
rsync -az --exclude __pycache__ --exclude .venv --exclude sessions --exclude media \
  ./ thomas@your-server-ip:/opt/videobot/
```

### Step 3: Configure Production Environment

```bash
# On the server
cd /opt/videobot

# Install Python dependencies
.venv/bin/pip install -r requirements.txt

# Create .env from template
cp .env.example .env
nano .env   # Fill in production values
chmod 600 .env   # Protect secrets

# Create required directories
mkdir -p sessions media
```

### Step 4: Set Up SSL Certificate

```bash
# Get SSL certificate from Let's Encrypt
sudo certbot certonly --standalone -d your-domain.com

# Or if nginx is already running:
sudo certbot --nginx -d your-domain.com
```

### Step 5: Configure Nginx

```bash
# Copy the nginx config (edit the domain name first)
sudo cp /opt/videobot/deploy/nginx_videobot.conf /etc/nginx/sites-available/videobot.conf

# Edit the server_name and SSL certificate paths
sudo nano /etc/nginx/sites-available/videobot.conf

# Enable the site
sudo ln -sf /etc/nginx/sites-available/videobot.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

The nginx config provides:
- HTTP → HTTPS redirect
- Reverse proxy to the FastAPI app on port 8080
- WebSocket support for live message updates
- ACME challenge passthrough for certbot renewals

### Step 6: Install systemd Services

```bash
# Web Manager
sudo cp /opt/videobot/deploy/videobot-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now videobot-web

# Video Bot (optional)
sudo cp /opt/videobot/deploy/videobot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now videobot

# Top-up Bot (optional — only if TELEGRAM_TOPUP_BOT_TOKEN is set)
sudo cp /opt/videobot/deploy/topup-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now topup-bot
```

### Step 7: Verify Deployment

```bash
# Check service status
sudo systemctl status videobot-web
sudo systemctl status videobot
sudo systemctl status topup-bot

# View logs
journalctl -u videobot-web -n 50 --no-pager
journalctl -u topup-bot -n 50 --no-pager

# Test HTTPS
curl -I https://your-domain.com/
```

### Updating (Re-deploy)

From your local machine:
```bash
./deploy/push_to_prod.sh
```

This script:
1. Syncs code to the server via rsync
2. Installs/updates Python dependencies
3. Updates admin + hub credentials in production `.env`
4. Installs/updates systemd services
5. Deploys nginx config
6. Restarts all services

---

## 🔐 Security Notes

| Item | Protection |
|------|-----------|
| **`.env`** | Contains all secrets. Never commit. `chmod 600`. |
| **`deploy/.deploy.env`** | Local deploy secrets (SSH pass, API keys). Gitignored. |
| **`sessions/`** | Contains live Telegram session files (equivalent to logged-in accounts). Gitignored. |
| **`*.session`** | Telethon session files — treat as passwords. Never share. |
| **OTP rate limit** | 6 requests per 30 minutes per IP (in `web_app.py`) |
| **MLBB lookup** | 30 requests per 30 minutes per IP |
| **Cookie security** | `httponly`, `samesite=lax`, `secure=true` in production |
| **Admin console** | Protected by HMAC-signed cookie derived from `ADMIN_SECRET` |

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **Bot Framework** | [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) v21.6 |
| **User-Account API** | [Telethon](https://github.com/LonamiWebs/Telethon) v1.37 |
| **Web Backend** | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) |
| **HTTP Client** | [HTTPX](https://www.python-httpx.org/) |
| **Frontend** | Vanilla HTML/CSS/JS (glassmorphism dark theme, Inter font) |
| **Reverse Proxy** | Nginx (HTTPS + WebSocket) |
| **Process Manager** | systemd |
| **SSL** | Let's Encrypt (certbot) |

---

## 📋 Environment Variables Reference

See [`.env.example`](.env.example) for the full list with inline documentation.

<details>
<summary>Click to expand full variable list</summary>

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_TOKEN` | — | Video bot token from @BotFather |
| `TELEGRAM_API_ID` | — | API ID from my.telegram.org |
| `TELEGRAM_API_HASH` | — | API hash from my.telegram.org |
| `MEDIA_DIR` | `./media` | Local video storage path |
| `STORAGE_CHANNEL_ID` | — | Private channel for video forwarding |
| `SOURCE_BOT` | `OnePiece_MMSub_s1_bot` | Source bot username |
| `RANGE_SIZE` | `25` | Episodes per range button |
| `TOTAL_EPISODES` | `0` (auto) | Total episode count override |
| `ALLOWED_USER_IDS` | — (all) | Comma-separated whitelist |
| `PORT` | `8080` | FastAPI listen port |
| `COOKIE_SECURE` | `true` | Secure cookie flag (set `false` for local HTTP) |
| `SESSIONS_DIR` | `sessions` | Per-user session file directory |
| `IDLE_TIMEOUT` | `1800` | Disconnect idle sessions (seconds) |
| `ADMIN_USERNAME` | `admin` | Operator console username |
| `ADMIN_PASSWORD` | — | Operator console password (empty = disabled) |
| `MLBB_HUB_URL` | `https://root.dkgamingshop.com/api` | Supplier hub API base |
| `MLBB_HUB_KEY` | — | Supplier hub API key |
| `TELEGRAM_TOPUP_BOT_TOKEN` | — | Top-up bot token |
| `TOPUP_ORDERS_ENABLED` | `false` | Enable real order placement |
| `TOPUP_PUBLIC_CLAIM` | `false` | Allow non-admin free claims |
| `TOPUP_ADMIN_IDS` | — | Admin Telegram user IDs |

</details>

---

## 📄 License

Private project. All rights reserved.
