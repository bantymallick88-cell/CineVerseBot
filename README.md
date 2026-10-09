# CineVerse Movie Search Telegram Auto-Filter Bot 🎬

An ultra-advanced, high-performance Telegram Auto-Filter Bot built with **Pyrogram** and **SQLite (WAL Mode + In-Memory Cache)**.

---

## 🌟 Key Features

1. **BotFather Menu Commands**:
   - Automatically registers Telegram Menu Commands on bot startup:
     - `/start` - Start the Bot & view stats
     - `/help` - How to use the bot
     - `/about` - Bot information
     - `/stats` - Check total indexed files & users
     - `/index` - Admin channel indexing sync

2. **Lifetime Database Persistence (`database.db`)**:
   - SQLite database permanently stored at: `database.db`.
   - Automatic migration preserves all records across restarts and reboots.
   - `PRAGMA journal_mode = WAL;` & `PRAGMA synchronous = NORMAL;`
   - Dedicated indexes: `CREATE INDEX IF NOT EXISTS idx_title ON movies(title);` for sub-second searches.

3. **Dynamic Peer Resolution**:
   - Automatically caches and resolves database channel peers (`@cenahub01` / ID `-1004312780149`) to permanently avoid `PeerIdInvalid`.

4. **High-Speed Chunk Indexing (200 Chunks)**:
   - `/index` scrapes the channel in dynamic 200-message batches with `executemany` database insertion.
   - Real-time status counter displaying scanned messages, newly added, and total database size.

4. **Pro-Level Aesthetic Branding & Unicode Styling**:
   - Premium typewriter typography header & footer:
     `───﹝ 🍿 𝙲𝙸𝙽𝙴𝚅𝙴𝚁𝚂𝙴 𝙼𝙾𝚅𝙸𝙴𝚂 ﹞───`
   - Popcorn result buttons: `🍿 [Movie Name] | [File Size]`
   - Branded footer buttons: `[ 🎬 CineVerse Channel ]` & `[ ⚡ Owner: Banty ]`

4. **Advanced Auto-Filter & Smart Pagination**:
   - Multi-word keyword search matching on filenames and captions with `COLLATE NOCASE`.
   - Formatted inline buttons: `🎬 <Movie Title> [<Size>]`.
   - `[◀️ Back]`, `[Page X/Y]`, and `[Next ▶️]` pagination controls.
   - Direct file copy via `copy_message` without leaking private channel links.
   - Live search logging in console: `[SEARCH] {text} from {chat_id}`.

5. **Auto & Manual Indexing**:
   - Automatic background indexing for new files/videos/audio uploaded in the DB Channel (`DB_CHANNEL_ID`).
   - Bot-compatible batch ID scraping in `/index` command with live progress updates.

---

## 🚀 Setup & Execution

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the Bot
```bash
python bot.py
```
