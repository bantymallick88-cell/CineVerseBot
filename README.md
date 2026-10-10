# CineVerse Movie Search Telegram Auto-Filter Bot 🎬

An ultra-advanced, high-performance Telegram Auto-Filter Bot built with **Pyrogram** and **SQLite (WAL Mode + Deduplication)**.

---

## 🌟 Key Features

1. **BotFather & Interactive Commands**:
   - `/start` - Start the Bot & view stats
   - `/help` - How to use the bot
   - `/about` - Bot information & developer details
   - `/stats` - Check total indexed files & registered users
   - `/disclaimer` - Legal non-hosting disclaimer
   - `/dmca` - Submit copyright removal notice with admin forwarding
   - `/index` - Admin channel indexing sync

2. **Interactive Sub-Filter Menus**:
   - **Quality**: `480p`, `720p`, `1080p`, `4K`, `HEVC`
   - **Languages**: `Odia`, `Hindi`, `English`, `Bengali`, `Tamil`, `Telugu`
   - **Season**: `Season 1` to `Season 15+`
   - Real-time sub-filter keyboards with `[ 🔙 Back to Results ]`.

3. **Dual 60-Second Auto-Delete**:
   - Automatically deletes user query and bot result messages after 60 seconds with graceful error handling.

4. **Zero-Duplicate Database Persistence (`database.db`)**:
   - SQLite database permanently stored at: `database.db`.
   - Automatic `rowid` deduplication prevents identical file/size duplicates.
   - `PRAGMA journal_mode = WAL;` & `PRAGMA synchronous = NORMAL;` for sub-second responses.

5. **Clean Presentation**:
   - Message text body kept clean without listing raw file names or sizes.
   - All files displayed exclusively on interactive inline download buttons: `📁 [Size] ▷ [Title]`.

---

## ☁️ 24/7 Deployment on Render.com

### Method 1: Using Render Blueprint (`render.yaml`) - Recommended
1. Push your repository to **GitHub**.
2. Sign in to [Render Dashboard](https://dashboard.render.com).
3. Click **"New +"** and select **"Blueprint"**.
4. Connect your GitHub repository (`CineVerseBot`).
5. Render will automatically detect `render.yaml` and configure the **Background Worker** service.
6. Fill in your environment variables when prompted and click **"Apply"**.

---

### Method 2: Manual Background Worker Creation
1. Go to [Render Dashboard](https://dashboard.render.com).
2. Click **"New +"** -> **"Background Worker"**.
3. Connect your GitHub repository.
4. Set the following settings:
   - **Name**: `cineverse-bot`
   - **Runtime**: `Docker` (or `Python 3`)
   - **Dockerfile Path**: `./Dockerfile`
   - **Instance Type**: `Free` / `Starter`
5. Add the Environment Variables:

| Environment Variable | Description / Sample Value |
| :--- | :--- |
| `API_ID` | Your Telegram API ID (`36971153`) |
| `API_HASH` | Your Telegram API Hash (`e5e4c9c88d0c5c2654d8fbe985523c9c`) |
| `BOT_TOKEN` | Your Bot Token from @BotFather |
| `DB_CHANNEL_ID` | Your DB Channel ID (e.g., `-1004312780149`) |
| `ADMIN_ID` | Primary Admin ID (`7831101047`) |
| `CHANNEL_LINK` | `https://t.me/CineVerseFlimSearch` |
| `DEVELOPER_LINK` | `tg://user?id=7831101047` |

6. Click **"Create Background Worker"**. Render will build the Docker container and keep your bot online 24/7 with automatic updates on every git push.

---

## 🚀 Local Setup & Execution

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
Create a `.env` file in the root directory with your credentials:
```env
API_ID=36971153
API_HASH=e5e4c9c88d0c5c2654d8fbe985523c9c
BOT_TOKEN=your_bot_token_here
DB_CHANNEL_ID=-1004312780149
ADMIN_ID=7831101047
CHANNEL_LINK=https://t.me/CineVerseFlimSearch
```

### 3. Run the Bot
```bash
python bot.py
```
