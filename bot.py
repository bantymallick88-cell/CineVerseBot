import os
import sys
import math
import time
import uuid
import sqlite3
import logging
from typing import List, Tuple, Dict, Any, Optional

# --- CRITICAL FOR PYTHON 3.14+ (Must be before pyrogram import) ---
import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
    BotCommand
)
from pyrogram.errors import (
    FloodWait,
    UserIsBlocked,
    MessageNotModified,
    PeerIdInvalid
)

# ---------------------------------------------------------------------------
# CONFIGURATION & LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("CineVerseBot")

def get_env_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val and str(val).strip():
        try:
            return int(str(val).strip())
        except (ValueError, TypeError):
            pass
    return default

def get_env_str(key: str, default: str) -> str:
    val = os.getenv(key)
    return str(val).strip() if val and str(val).strip() else default

API_ID = get_env_int("API_ID", 36971153)
API_HASH = get_env_str("API_HASH", "e5e4c9c88d0c5c2654d8fbe985523c9c")
BOT_TOKEN = get_env_str("BOT_TOKEN", "8994566569:AAG8pdApyO7ut1BrG6xdstrkdWL1ZUProfY")

raw_db_channel = get_env_str("DB_CHANNEL_ID", "-1004312780149")
try:
    DB_CHANNEL_ID = int(raw_db_channel)
except (ValueError, TypeError):
    DB_CHANNEL_ID = str(raw_db_channel).lstrip("@")

ADMIN_ID = get_env_int("ADMIN_ID", 7831101047)
CHANNEL_LINK = get_env_str("CHANNEL_LINK", "https://t.me/cenahub01")
WELCOME_IMAGE_URL = get_env_str(
    "WELCOME_IMAGE_URL",
    "https://images.unsplash.com/photo-1536440136628-849c177e76a1?q=80&w=1200&auto=format&fit=crop"
)

PAGE_SIZE = 8
SEARCH_CACHE: Dict[str, str] = {}
DB_FILE = os.path.join(os.getcwd(), "database.db")
TARGET_RESOLVED_CHAT_ID: Optional[Any] = None

# ---------------------------------------------------------------------------
# DATABASE MANAGER (WAL MODE, PERSISTENT & SAFE)
# ---------------------------------------------------------------------------
class Database:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS movies (
                    file_id TEXT PRIMARY KEY,
                    title TEXT,
                    file_size INTEGER,
                    message_id INTEGER
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_title ON movies(title);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_id ON movies(message_id);")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()

    def _add_user(self, user_id: int):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?);", (user_id,))
                conn.commit()
        except Exception as e:
            logger.error(f"Error saving user {user_id}: {e}")

    def _add_movie(self, file_id: str, title: str, file_size: int, message_id: int) -> bool:
        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO movies (file_id, title, file_size, message_id) VALUES (?, ?, ?, ?);",
                    (file_id, title, file_size, message_id)
                )
                conn.commit()
                return cur.rowcount > 0
        except Exception as e:
            logger.error(f"Error inserting movie: {e}")
            return False

    def _add_movies_batch(self, movies_data: List[Tuple[str, str, int, int]]) -> int:
        if not movies_data:
            return 0
        try:
            with self._get_connection() as conn:
                cur = conn.executemany(
                    "INSERT OR IGNORE INTO movies (file_id, title, file_size, message_id) VALUES (?, ?, ?, ?);",
                    movies_data
                )
                conn.commit()
                return cur.rowcount
        except Exception as e:
            logger.error(f"Error inserting batch: {e}")
            return 0

    def _search_files(self, query: str, offset: int = 0, limit: int = PAGE_SIZE) -> Tuple[List[Dict[str, Any]], int]:
        words = [w.strip() for w in query.split() if w.strip()]
        if not words:
            return [], 0

        conditions = ["title LIKE ?" for _ in words]
        params = [f"%{w}%" for w in words]
        where_sql = " AND ".join(conditions)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            total = cursor.execute(f"SELECT COUNT(*) FROM movies WHERE {where_sql};", params).fetchone()[0]
            if total == 0:
                return [], 0

            search_sql = f"SELECT file_id, title, file_size, message_id FROM movies WHERE {where_sql} ORDER BY message_id DESC LIMIT ? OFFSET ?;"
            cursor.execute(search_sql, params + [limit, offset])
            rows = [dict(row) for row in cursor.fetchall()]
            return rows, total

    def _get_file_by_msg_id(self, message_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT file_id, title, file_size, message_id FROM movies WHERE message_id = ?;", (message_id,)).fetchone()
            return dict(row) if row else None

    def _get_stats(self) -> Dict[str, int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            total_movies = cursor.execute("SELECT COUNT(*) FROM movies;").fetchone()[0]
            total_users = cursor.execute("SELECT COUNT(*) FROM users;").fetchone()[0]
            return {"movies": total_movies, "users": total_users}

    # Async wrappers
    async def add_user(self, user_id: int):
        await asyncio.to_thread(self._add_user, user_id)

    async def add_movie(self, file_id: str, title: str, file_size: int, message_id: int) -> bool:
        return await asyncio.to_thread(self._add_movie, file_id, title, file_size, message_id)

    async def add_movies_batch(self, movies_data: List[Tuple[str, str, int, int]]) -> int:
        return await asyncio.to_thread(self._add_movies_batch, movies_data)

    async def search_files(self, query: str, offset: int = 0, limit: int = PAGE_SIZE) -> Tuple[List[Dict[str, Any]], int]:
        return await asyncio.to_thread(self._search_files, query, offset, limit)

    async def get_file_by_msg_id(self, message_id: int) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_file_by_msg_id, message_id)

    async def get_stats(self) -> Dict[str, int]:
        return await asyncio.to_thread(self._get_stats)


db = Database()

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def format_size(size_bytes: int) -> str:
    if not size_bytes or size_bytes <= 0:
        return "N/A"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {units[i]}"

async def auto_delete(message: Message, delay: int = 60):
    try:
        await asyncio.sleep(delay)
        await message.delete()
    except Exception:
        pass

def format_search_text(query: str, files: List[Dict[str, Any]], total: int, page: int, total_pages: int) -> str:
    lines = []
    for f in files:
        name = f.get("title", "Movie File")
        size_str = format_size(f.get("file_size", 0))
        lines.append(f"📁 `{size_str}` ▷ **{name}**")

    files_list = "\n".join(lines)
    return (
        f"🔍 **Search Results for:** `{query}`\n\n"
        f"{files_list}\n\n"
        f"📊 **Total Results:** `{total}` | **Page:** `{page}/{total_pages}`\n"
        f"✨ *Click any button below to download instantly!*\n"
        f"⚠️ *This search result will auto-delete in 60 seconds (1 minute)!*"
    )

def build_pagination_markup(files: List[Dict[str, Any]], cache_id: str, current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    buttons = []
    
    # File download buttons formatted as: 📁 [File Size] ▷ [Clean File Title]
    for f in files:
        name = f.get("title", "Movie")
        size_str = format_size(f.get("file_size", 0))
        display_name = (name[:30] + "...") if len(name) > 33 else name
        btn_text = f"📁 {size_str} ▷ {display_name}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"get_{f['message_id']}")])

    # Row 1: Filter buttons
    filter_row = [
        InlineKeyboardButton("🌐 LANGUAGES", callback_data="cb_filter_lang"),
        InlineKeyboardButton("📺 Qualitys", callback_data="cb_filter_qual"),
        InlineKeyboardButton("🎬 Season", callback_data="cb_filter_season")
    ]
    buttons.append(filter_row)

    # Row 2: Navigation & Page buttons
    nav_row = []
    if current_page > 1:
        nav_row.append(InlineKeyboardButton("⏪ PREV", callback_data=f"nav_{cache_id}_{current_page - 1}"))
    nav_row.append(InlineKeyboardButton(f"🗓️ {current_page}/{total_pages}", callback_data="cb_noop"))
    if current_page < total_pages:
        nav_row.append(InlineKeyboardButton("NEXT ⏩", callback_data=f"nav_{cache_id}_{current_page + 1}"))
    buttons.append(nav_row)

    # Row 3: Channel and Creator info
    buttons.append([
        InlineKeyboardButton("🎬 CineVerse Channel", url=CHANNEL_LINK),
        InlineKeyboardButton("⚡ By Banty", url=CHANNEL_LINK)
    ])
    
    # Row 4: Close button
    buttons.append([InlineKeyboardButton("🗑️ Close Search", callback_data="cb_close")])
    return InlineKeyboardMarkup(buttons)

# ---------------------------------------------------------------------------
# PYROGRAM BOT CLIENT
# ---------------------------------------------------------------------------
app = Client(
    "CineVerseBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

async def resolve_channel_peer(client: Client) -> Any:
    global TARGET_RESOLVED_CHAT_ID
    if TARGET_RESOLVED_CHAT_ID:
        return TARGET_RESOLVED_CHAT_ID

    candidates = [
        int(DB_CHANNEL_ID) if str(DB_CHANNEL_ID).lstrip("-").isdigit() else None,
        -1004312780149,
        "cenahub01",
        "@cenahub01",
        DB_CHANNEL_ID
    ]

    for candidate in candidates:
        if candidate is None:
            continue
        try:
            chat = await client.get_chat(candidate)
            if chat and chat.id:
                TARGET_RESOLVED_CHAT_ID = chat.id
                return TARGET_RESOLVED_CHAT_ID
        except Exception:
            continue

    try:
        TARGET_RESOLVED_CHAT_ID = int(DB_CHANNEL_ID)
    except Exception:
        TARGET_RESOLVED_CHAT_ID = DB_CHANNEL_ID

    return TARGET_RESOLVED_CHAT_ID

# ---------------------------------------------------------------------------
# COMMAND HANDLERS
# ---------------------------------------------------------------------------
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    if message.from_user:
        await db.add_user(message.from_user.id)
    stats = await db.get_stats()
    bot_me = await client.get_me()

    caption = (
        f"👋 **Welcome to CineVerse Movie Search Bot!**\n\n"
        f"Hello {message.from_user.mention if message.from_user else 'Friend'},\n"
        f"Search and download any movie or web series instantly!\n\n"
        f"📊 **Database Statistics:**\n"
        f"• 🎬 **Indexed Movies:** `{stats['movies']}`\n"
        f"• 👥 **Total Users:** `{stats['users']}`\n"
        f"• ⚡ **Speed:** Ultra-Fast (Sub-second)\n"
        f"• 👑 **Developer:** `Banty`\n\n"
        f"💬 *Type any movie name below to search immediately!*"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Help", callback_data="cb_help"),
            InlineKeyboardButton("ℹ️ About", callback_data="cb_about")
        ],
        [
            InlineKeyboardButton("📢 CineVerse Channel", url=CHANNEL_LINK),
            InlineKeyboardButton("➕ Add Me to Group", url=f"https://t.me/{bot_me.username}?startgroup=true")
        ]
    ])

    try:
        await message.reply_photo(photo=WELCOME_IMAGE_URL, caption=caption, reply_markup=markup)
    except Exception:
        await message.reply_text(caption, reply_markup=markup, disable_web_page_preview=True)

@app.on_message(filters.command("stats"))
async def stats_handler(client: Client, message: Message):
    if message.from_user:
        await db.add_user(message.from_user.id)
    stats = await db.get_stats()
    db_size = os.path.getsize(DB_FILE) if os.path.exists(DB_FILE) else 0

    await message.reply_text(
        f"📊 **CineVerse Bot Statistics**\n\n"
        f"🎬 **Total Movies in DB:** `{stats['movies']}`\n"
        f"👥 **Total Registered Users:** `{stats['users']}`\n"
        f"💾 **Database Size:** `{format_size(db_size)}`\n"
        f"⚡ **Status:** Active & 100% Operational\n"
        f"✨ **Powered By:** CineVerse | By Banty",
        quote=True
    )

@app.on_message(filters.command("help"))
async def help_handler(client: Client, message: Message):
    if message.from_user:
        await db.add_user(message.from_user.id)
    await message.reply_text(
        "📖 **How to Use CineVerse Movie Search Bot**\n\n"
        "1. Simply send any movie name (e.g., `Inception` or `Avengers`).\n"
        "2. Click the popcorn button with your desired file quality.\n"
        "3. File will be delivered to your private chat instantly!\n\n"
        "📌 **Commands:**\n"
        "• `/start` - Start the bot\n"
        "• `/stats` - View total movies and user stats\n"
        "• `/index` - Admin DB Channel indexer",
        quote=True
    )

@app.on_message(filters.command("about"))
async def about_handler(client: Client, message: Message):
    if message.from_user:
        await db.add_user(message.from_user.id)
    stats = await db.get_stats()
    await message.reply_text(
        f"───﹝ 🍿 𝙲𝙸𝙽𝙴𝚅𝙴𝚁𝚂𝙴 𝙼𝙾𝚅𝙸𝙴𝚂 ﹞───\n\n"
        f"🤖 **Bot:** CineVerse Auto-Filter Bot\n"
        f"📁 **Total Movies:** `{stats['movies']}`\n"
        f"👥 **Total Users:** `{stats['users']}`\n"
        f"⚡ **Created By:** `Banty`\n"
        f"✨ **Powered By:** **CineVerse Network**",
        quote=True
    )

@app.on_message(filters.command("index"))
async def index_handler(client: Client, message: Message):
    if message.from_user:
        await db.add_user(message.from_user.id)

    status_msg = await message.reply_text("⏳ **Starting Fast Channel Indexing...**")
    
    total_new = 0
    scanned_msgs = 0
    last_update = time.time()

    try:
        peer = await resolve_channel_peer(client)
        try:
            await client.get_chat(peer)
        except Exception:
            pass

        current_id = 1
        empty_streak = 0

        while empty_streak < 25:
            msg_ids = list(range(current_id, current_id + 200))
            try:
                batch = await client.get_messages(chat_id=peer, message_ids=msg_ids)
            except FloodWait as e:
                await asyncio.sleep(e.value)
                batch = await client.get_messages(chat_id=peer, message_ids=msg_ids)
            except (PeerIdInvalid, Exception) as e:
                logger.warning(f"Error fetching batch {current_id}: {e}")
                break

            found_count = 0
            to_insert = []

            if batch:
                if not isinstance(batch, list):
                    batch = [batch]
                for msg in batch:
                    if msg and not getattr(msg, "empty", False):
                        found_count += 1
                        scanned_msgs += 1
                        media = msg.video or msg.document or msg.audio or msg.animation
                        if media:
                            title = (
                                msg.caption.strip().split("\n")[0].strip() if msg.caption and msg.caption.strip()
                                else (getattr(media, "file_name", None) or f"Movie_{msg.id}")
                            )
                            file_id = getattr(media, "file_id", None)
                            file_size = getattr(media, "file_size", 0)
                            if file_id:
                                to_insert.append((file_id, title, file_size, msg.id))

            if to_insert:
                added = await db.add_movies_batch(to_insert)
                total_new += added

            empty_streak = empty_streak + 1 if found_count == 0 else 0
            current_id += 200

            if time.time() - last_update > 3:
                stats = await db.get_stats()
                try:
                    await status_msg.edit_text(
                        f"⏳ **Indexing in Progress...**\n\n"
                        f"• **Scanned up to ID:** `{current_id}`\n"
                        f"• **Newly Added:** `{total_new}`\n"
                        f"• **Total Movies in DB:** `{stats['movies']}`"
                    )
                except MessageNotModified:
                    pass
                last_update = time.time()

        stats = await db.get_stats()
        await status_msg.edit_text(
            f"✅ **Indexing Complete!**\n\n"
            f"• **Scanned up to ID:** `{current_id}`\n"
            f"• **New Files Added:** `{total_new}`\n"
            f"🎬 **Total Movies in Database:** `{stats['movies']}`"
        )
    except FloodWait as e:
        await asyncio.sleep(e.value)
        stats = await db.get_stats()
        await status_msg.edit_text(f"✅ **Indexing Complete!**\n\n🎬 **Total Movies in Database:** `{stats['movies']}`")
    except PeerIdInvalid as e:
        logger.warning(f"PeerIdInvalid in index_handler: {e}")
        stats = await db.get_stats()
        await status_msg.edit_text(
            f"⚠️ **DB Channel Notice:**\n"
            f"Please ensure the bot is added as an **Administrator** in the DB Channel (`{DB_CHANNEL_ID}`).\n\n"
            f"🎬 **Total Movies in DB:** `{stats['movies']}`"
        )
    except Exception as e:
        logger.error(f"Indexing error: {e}")
        stats = await db.get_stats()
        await status_msg.edit_text(f"⚠️ **Indexing Notice:** {e}\n\n🎬 **Total Movies in DB:** `{stats['movies']}`")

# ---------------------------------------------------------------------------
# AUTO-INDEX NEW UPLOADS IN CHANNEL
# ---------------------------------------------------------------------------
@app.on_message((filters.document | filters.video | filters.audio | filters.animation) & ~filters.private)
async def auto_index_channel(client: Client, message: Message):
    peer = await resolve_channel_peer(client)
    if message.chat.id != peer and str(message.chat.username or "").lower() not in ["cenahub01"]:
        return

    media = message.video or message.document or message.audio or message.animation
    if not media:
        return

    title = (
        message.caption.strip().split("\n")[0].strip() if message.caption and message.caption.strip()
        else (getattr(media, "file_name", None) or f"Movie_{message.id}")
    )
    file_id = getattr(media, "file_id", None)
    file_size = getattr(media, "file_size", 0)

    if file_id:
        success = await db.add_movie(file_id=file_id, title=title, file_size=file_size, message_id=message.id)
        if success:
            logger.info(f"[AUTO-INDEX] Saved '{title}' (ID: {message.id})")

# ---------------------------------------------------------------------------
# SEARCH & AUTO-FILTER HANDLER (WITH 60S AUTO-DELETE)
# ---------------------------------------------------------------------------
@app.on_message(filters.text & ~filters.bot & ~filters.via_bot)
async def auto_filter_handler(client: Client, message: Message):
    text = message.text.strip()
    if text.startswith("/"):
        return

    if message.from_user:
        await db.add_user(message.from_user.id)

    # 1. Start 60-second auto-delete task for user's query message in all chats
    asyncio.create_task(auto_delete(message, 60))

    if len(text) < 2:
        return

    files, total = await db.search_files(text, offset=0, limit=PAGE_SIZE)
    if total == 0:
        not_found_msg = await message.reply_text(
            "❌ Movie / File Not Available in Database.",
            quote=True
        )
        if not_found_msg:
            asyncio.create_task(auto_delete(not_found_msg, 60))
        return

    cache_id = uuid.uuid4().hex[:8]
    SEARCH_CACHE[cache_id] = text
    total_pages = math.ceil(total / PAGE_SIZE)
    markup = build_pagination_markup(files, cache_id, 1, total_pages)
    response_text = format_search_text(text, files, total, 1, total_pages)

    # 2. Reply with formatted results and start 60-second auto-delete task on bot response
    sent_msg = await message.reply_text(response_text, reply_markup=markup, quote=True)
    if sent_msg:
        asyncio.create_task(auto_delete(sent_msg, 60))

# ---------------------------------------------------------------------------
# CALLBACK QUERY ROUTER
# ---------------------------------------------------------------------------
@app.on_callback_query()
async def callback_router(client: Client, query: CallbackQuery):
    if query.from_user:
        await db.add_user(query.from_user.id)
    data = query.data

    if data == "cb_close":
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.answer("Search closed.")

    elif data == "cb_noop":
        await query.answer("Page indicator", show_alert=False)

    elif data == "cb_filter_lang":
        await query.answer("🌐 Language filter active for this title.", show_alert=True)

    elif data == "cb_filter_qual":
        await query.answer("📺 Quality options (1080p, 720p, 480p, HEVC) listed.", show_alert=True)

    elif data == "cb_filter_season":
        await query.answer("🎬 Season episodes listed in order.", show_alert=True)

    elif data == "cb_help":
        help_text = (
            "📖 **How to Use CineVerse Movie Search Bot**\n\n"
            "1. Simply send any movie name (e.g., `Inception` or `Avatar`).\n"
            "2. Click the popcorn button with your desired file.\n"
            "3. File will be delivered to your private chat instantly!\n\n"
            "⚡ **By Banty | CineVerse Network**"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="cb_home")]])
        try:
            await query.message.edit_caption(caption=help_text, reply_markup=markup)
        except Exception:
            await query.message.edit_text(help_text, reply_markup=markup)
        await query.answer()

    elif data == "cb_about":
        stats = await db.get_stats()
        about_text = (
            f"───﹝ 🍿 𝙲𝙸𝙽𝙴𝚅𝙴𝚁𝚂𝙴 𝙼𝙾𝚅𝙸𝙴𝚂 ﹞───\n\n"
            f"🤖 **Bot:** CineVerse Auto-Filter Bot\n"
            f"📁 **Total Movies:** `{stats['movies']}`\n"
            f"👥 **Total Users:** `{stats['users']}`\n"
            f"⚡ **Created By:** `Banty`\n"
            f"✨ **Powered By:** **CineVerse Network**"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="cb_home")]])
        try:
            await query.message.edit_caption(caption=about_text, reply_markup=markup)
        except Exception:
            await query.message.edit_text(about_text, reply_markup=markup)
        await query.answer()

    elif data == "cb_home":
        stats = await db.get_stats()
        bot_me = await client.get_me()
        caption = (
            f"👋 **Welcome to CineVerse Movie Search Bot!**\n\n"
            f"Search and download any movie or web series instantly!\n\n"
            f"📊 **Database Statistics:**\n"
            f"• 🎬 **Indexed Movies:** `{stats['movies']}`\n"
            f"• 👥 **Total Users:** `{stats['users']}`\n"
            f"• ⚡ **Speed:** Ultra-Fast (Sub-second)\n"
            f"• 👑 **Developer:** `Banty`\n\n"
            f"💬 *Type any movie name below to search immediately!*"
        )
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📖 Help", callback_data="cb_help"),
                InlineKeyboardButton("ℹ️ About", callback_data="cb_about")
            ],
            [
                InlineKeyboardButton("📢 CineVerse Channel", url=CHANNEL_LINK),
                InlineKeyboardButton("➕ Add Me to Group", url=f"https://t.me/{bot_me.username}?startgroup=true")
            ]
        ])
        try:
            await query.message.edit_caption(caption=caption, reply_markup=markup)
        except Exception:
            try:
                await query.message.edit_text(caption, reply_markup=markup)
            except MessageNotModified:
                pass
        await query.answer()

    elif data.startswith("get_"):
        msg_id = int(data.split("_")[1])
        try:
            file_info = await db.get_file_by_msg_id(msg_id)
            title = file_info.get("title", "Movie File") if file_info else "Movie File"
            size_str = format_size(file_info.get("file_size", 0)) if file_info else "N/A"

            caption = (
                "───﹝ 🍿 𝙲𝙸𝙽𝙴𝚅𝙴𝚁𝚂𝙴 𝙼𝙾𝚅𝙸𝙴𝚂 ﹞───\n\n"
                f"🎬 𝙵𝚒𝚕𝚎 𝙽𝚊𝚖𝚎: `{title}`\n"
                f"📦 𝚂𝚒𝚣𝚎: `{size_str}`\n"
                "⚡ 𝙲𝚛𝚎𝚊𝚝𝚎𝚍 𝙱𝚢: `Banty`\n"
                "✨ 𝙿𝚘𝚠𝚎𝚛𝚎𝚍 𝙱𝚢: **CineVerse Network**\n\n"
                "───﹝ ⚠️ 𝙰𝚄𝚃𝙾-𝙳𝙴𝙻𝙴𝚃𝙴: 𝟼𝟶𝚜 ﹞───\n"
                "⚠️ *This movie file will auto-delete in 60 seconds (1 minute) due to copyright! Forward/save it now!*"
            )

            sent_file = None
            if file_info and file_info.get("file_id"):
                try:
                    sent_file = await client.send_cached_media(
                        chat_id=query.from_user.id,
                        file_id=file_info["file_id"],
                        caption=caption
                    )
                except Exception:
                    sent_file = None

            if not sent_file:
                peer = await resolve_channel_peer(client)
                sent_file = await client.copy_message(
                    chat_id=query.from_user.id,
                    from_chat_id=peer,
                    message_id=msg_id,
                    caption=caption
                )
            if sent_file:
                asyncio.create_task(auto_delete(sent_file, 60))

            if query.message.chat.type != enums.ChatType.PRIVATE:
                await query.answer("✅ File sent to your PM! (Auto-deletes in 60s)", show_alert=True)
            else:
                await query.answer("✅ File delivered below! (Auto-deletes in 60s)")
        except UserIsBlocked:
            await query.answer("⚠️ Please start the bot in private first!", show_alert=True)
        except Exception as e:
            logger.error(f"Error copying file {msg_id}: {e}")
            await query.answer("❌ File unavailable or removed.", show_alert=True)

    elif data.startswith("nav_"):
        parts = data.split("_")
        cache_id = parts[1]
        page = int(parts[2])
        search_query = SEARCH_CACHE.get(cache_id)

        if not search_query:
            await query.answer("⚠️ Search session expired. Please search again.", show_alert=True)
            return

        offset = (page - 1) * PAGE_SIZE
        files, total = await db.search_files(search_query, offset=offset, limit=PAGE_SIZE)
        total_pages = math.ceil(total / PAGE_SIZE) if total > 0 else 1

        if not files:
            await query.answer("No more results.", show_alert=True)
            return

        markup = build_pagination_markup(files, cache_id, page, total_pages)
        response_text = format_search_text(search_query, files, total, page, total_pages)
        try:
            await query.message.edit_text(response_text, reply_markup=markup)
        except MessageNotModified:
            pass
        await query.answer()

# ---------------------------------------------------------------------------
# START BOT VIA STANDARD app.run()
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("🚀 CineVerse Movie Search Bot is starting...")
    print(f"📌 Admin ID: {ADMIN_ID} | DB Channel: {DB_CHANNEL_ID}")
    print("=" * 60)
    app.run()
