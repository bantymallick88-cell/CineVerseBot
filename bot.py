import os
import sys
import math
import time
import uuid
import sqlite3
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
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
    MessageDeleteForbidden,
    MessageIdInvalid,
    RPCError,
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
CHANNEL_LINK = get_env_str("CHANNEL_LINK", "https://t.me/CineVerseFlimSearch")
DEVELOPER_LINK = get_env_str("DEVELOPER_LINK", f"tg://user?id={ADMIN_ID}")
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
            # Clean up existing exact duplicates so only 1 unique record remains per (title, file_size)
            try:
                conn.execute("""
                    DELETE FROM movies 
                    WHERE rowid NOT IN (
                        SELECT MIN(rowid) 
                        FROM movies 
                        GROUP BY title, file_size
                    );
                """)
            except Exception as e:
                logger.warning(f"Note on deduplicating movies table: {e}")
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

        conditions = []
        params = []
        for w in words:
            if w.upper().startswith("S") and len(w) == 3 and w[1:].isdigit():
                season_num = int(w[1:])
                conditions.append("(title LIKE ? OR title LIKE ? OR title LIKE ? OR title LIKE ?)")
                params.extend([
                    f"%S{season_num:02d}%",
                    f"%S{season_num}%",
                    f"%Season {season_num}%",
                    f"%Season {season_num:02d}%"
                ])
            elif w.upper() in ["S15+", "S15"]:
                conditions.append("(title LIKE '%S15%' OR title LIKE '%Season 15%' OR title LIKE '%S16%' OR title LIKE '%Season 16%')")
            elif w.upper() == "4K":
                conditions.append("(title LIKE '%4K%' OR title LIKE '%2160p%' OR title LIKE '%UHD%')")
            elif w.upper() == "HEVC":
                conditions.append("(title LIKE '%HEVC%' OR title LIKE '%x265%')")
            elif w.lower() == "odia":
                conditions.append("(title LIKE '%Odia%' OR title LIKE '%Oriya%')")
            elif w.lower() == "bengali":
                conditions.append("(title LIKE '%Bengali%' OR title LIKE '%Bangla%')")
            else:
                conditions.append("title LIKE ?")
                params.append(f"%{w}%")

        where_sql = " AND ".join(conditions)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            count_sql = f"SELECT COUNT(*) FROM (SELECT 1 FROM movies WHERE {where_sql} GROUP BY title, file_size);"
            total = cursor.execute(count_sql, params).fetchone()[0]
            if total == 0:
                return [], 0

            search_sql = f"""
                SELECT file_id, title, file_size, MAX(message_id) AS message_id 
                FROM movies 
                WHERE {where_sql} 
                GROUP BY title, file_size 
                ORDER BY message_id DESC 
                LIMIT ? OFFSET ?;
            """
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

async def auto_delete(message: Optional[Message], delay: int = 60):
    if not message:
        return
    try:
        await asyncio.sleep(delay)
        await message.delete()
    except (MessageDeleteForbidden, MessageIdInvalid, RPCError):
        pass
    except Exception as e:
        logger.debug(f"Auto-delete bypassed for message {getattr(message, 'id', 'unknown')}: {e}")

def format_search_text(query: str, total: int, page: int, total_pages: int) -> str:
    return (
        f"🔍 **Search Results for:** `{query}`\n\n"
        f"📊 **Total Results:** `{total}` | **Page:** `{page}/{total_pages}`\n"
        f"✨ *Click any button below to download instantly!*\n"
        f"⚠️ *This search result will auto-delete in 60 seconds (1 minute)!*\n"
        f"⚖️ *Notice: We do not host any content. For DMCA/Takedown, use /dmca.*"
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

    # Row 1: Filter buttons linking to interactive submenus
    filter_row = [
        InlineKeyboardButton("🌐 LANGUAGES", callback_data=f"flt_m_lang_{cache_id}"),
        InlineKeyboardButton("📺 Qualitys", callback_data=f"flt_m_qual_{cache_id}"),
        InlineKeyboardButton("🎬 Season", callback_data=f"flt_m_season_{cache_id}")
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
        InlineKeyboardButton("⚡ By Banty", url=DEVELOPER_LINK)
    ])
    
    # Row 4: Close button
    buttons.append([InlineKeyboardButton("🗑️ Close Search", callback_data="cb_close")])
    return InlineKeyboardMarkup(buttons)

def build_quality_menu(cache_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("480p", callback_data=f"flt_set_{cache_id}_480p"),
            InlineKeyboardButton("720p", callback_data=f"flt_set_{cache_id}_720p"),
            InlineKeyboardButton("1080p", callback_data=f"flt_set_{cache_id}_1080p")
        ],
        [
            InlineKeyboardButton("4K", callback_data=f"flt_set_{cache_id}_4K"),
            InlineKeyboardButton("HEVC", callback_data=f"flt_set_{cache_id}_HEVC")
        ],
        [
            InlineKeyboardButton("🔙 Back to Results", callback_data=f"flt_back_{cache_id}")
        ]
    ])

def build_language_menu(cache_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Odia", callback_data=f"flt_set_{cache_id}_Odia"),
            InlineKeyboardButton("Hindi", callback_data=f"flt_set_{cache_id}_Hindi"),
            InlineKeyboardButton("English", callback_data=f"flt_set_{cache_id}_English")
        ],
        [
            InlineKeyboardButton("Bengali", callback_data=f"flt_set_{cache_id}_Bengali"),
            InlineKeyboardButton("Tamil", callback_data=f"flt_set_{cache_id}_Tamil"),
            InlineKeyboardButton("Telugu", callback_data=f"flt_set_{cache_id}_Telugu")
        ],
        [
            InlineKeyboardButton("🔙 Back to Results", callback_data=f"flt_back_{cache_id}")
        ]
    ])

def build_season_menu(cache_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Season 1", callback_data=f"flt_set_{cache_id}_S01"),
            InlineKeyboardButton("Season 2", callback_data=f"flt_set_{cache_id}_S02"),
            InlineKeyboardButton("Season 3", callback_data=f"flt_set_{cache_id}_S03")
        ],
        [
            InlineKeyboardButton("Season 4", callback_data=f"flt_set_{cache_id}_S04"),
            InlineKeyboardButton("Season 5", callback_data=f"flt_set_{cache_id}_S05"),
            InlineKeyboardButton("Season 6", callback_data=f"flt_set_{cache_id}_S06")
        ],
        [
            InlineKeyboardButton("Season 7", callback_data=f"flt_set_{cache_id}_S07"),
            InlineKeyboardButton("Season 8", callback_data=f"flt_set_{cache_id}_S08"),
            InlineKeyboardButton("Season 9", callback_data=f"flt_set_{cache_id}_S09")
        ],
        [
            InlineKeyboardButton("Season 10", callback_data=f"flt_set_{cache_id}_S10"),
            InlineKeyboardButton("Season 11", callback_data=f"flt_set_{cache_id}_S11"),
            InlineKeyboardButton("Season 12", callback_data=f"flt_set_{cache_id}_S12")
        ],
        [
            InlineKeyboardButton("Season 13", callback_data=f"flt_set_{cache_id}_S13"),
            InlineKeyboardButton("Season 14", callback_data=f"flt_set_{cache_id}_S14"),
            InlineKeyboardButton("Season 15+", callback_data=f"flt_set_{cache_id}_S15+")
        ],
        [
            InlineKeyboardButton("🔙 Back to Results", callback_data=f"flt_back_{cache_id}")
        ]
    ])

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
        f"⚖️ **Disclaimer:** *This bot does not store or host any files on its servers. All media files are indexed from third-party channels on Telegram. If you are a copyright owner and want to report/remove content, please use* `/dmca` *command.*\n\n"
        f"💬 *Type any movie name below to search immediately!*"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Help", callback_data="cb_help"),
            InlineKeyboardButton("ℹ️ About", callback_data="cb_about")
        ],
        [
            InlineKeyboardButton("⚖️ Disclaimer", callback_data="cb_disclaimer"),
            InlineKeyboardButton("📋 DMCA", callback_data="cb_dmca")
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
        "• `/disclaimer` - Legal non-hosting disclaimer\n"
        "• `/dmca` - Submit copyright takedown request\n"
        "• `/index` - Admin DB Channel indexer",
        quote=True
    )

@app.on_message(filters.command("disclaimer"))
async def disclaimer_handler(client: Client, message: Message):
    if message.from_user:
        await db.add_user(message.from_user.id)
    text = (
        "⚖️ **Legal Disclaimer & Terms of Service**\n\n"
        "• **Non-Hosting Policy:** This bot does not store, host, upload, or reproduce any media files or video content on its servers.\n"
        "• **Telegram Indexing:** All media files and links provided are indexed automatically from publicly accessible third-party channels on Telegram.\n"
        "• **Copyright Compliance:** CineVerse respects intellectual property rights. If you are a copyright owner or authorized representative and wish to request removal of indexed files, please use the `/dmca` command to submit a takedown notice.\n\n"
        "⚡ **Powered By:** **CineVerse Network**"
    )
    await message.reply_text(text, quote=True)

@app.on_message(filters.command("dmca"))
async def dmca_handler(client: Client, message: Message):
    if message.from_user:
        await db.add_user(message.from_user.id)

    cmd_args = message.text.split(maxsplit=1)
    user_info = message.from_user

    if len(cmd_args) > 1 and cmd_args[1].strip():
        report_details = cmd_args[1].strip()
        
        # Format log for primary admin ID 7831101047
        admin_log = (
            "🚨 **NEW DMCA / CONTENT TAKEDOWN REQUEST**\n\n"
            f"👤 **From User:** {user_info.mention if user_info else 'Unknown'}\n"
            f"🆔 **User ID:** `{user_info.id if user_info else 'N/A'}`\n"
            f"🏷️ **Username:** @{user_info.username if user_info and user_info.username else 'None'}\n"
            f"📅 **Date:** `{message.date}`\n\n"
            f"📝 **Report / Removal Details:**\n"
            f"```\n{report_details}\n```"
        )
        
        try:
            await client.send_message(chat_id=ADMIN_ID, text=admin_log)
        except Exception as e:
            logger.error(f"Failed to forward DMCA request to admin: {e}")

        confirm_text = (
            "✅ **DMCA Removal Request Submitted!**\n\n"
            "Thank you for contacting us. Your content removal notice has been logged and forwarded directly to the administrator.\n"
            "We will review and delist the indexed content promptly.\n\n"
            "⚡ **CineVerse Administration**"
        )
        await message.reply_text(confirm_text, quote=True)
    else:
        guide_text = (
            "📋 **DMCA & Copyright Infringement Takedown Notice**\n\n"
            "If you are a copyright owner or an agent thereof and believe that any content indexed by CineVerse infringes upon your copyrights, please submit your request:\n\n"
            "📌 **How to submit:**\n"
            "Send `/dmca <Movie/Series Name, File Details, or Link>`\n\n"
            "**Example:**\n"
            "`/dmca Please remove the movie 'Movie Title (2024)' from index.`\n\n"
            "Your notice will be immediately forwarded to the bot administrator for prompt review and delisting."
        )
        await message.reply_text(guide_text, quote=True)

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
    response_text = format_search_text(text, total, 1, total_pages)

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

    elif data.startswith("flt_menu_"):
        parts = data.split("_")
        menu_type = parts[2]
        cache_id = parts[3]
        search_query = SEARCH_CACHE.get(cache_id)

        if not search_query:
            await query.answer("⚠️ Search session expired. Please search again.", show_alert=True)
            return

        if menu_type == "qual":
            menu_text = (
                f"📺 **Select Quality for:** `{search_query}`\n\n"
                f"✨ *Choose a quality below to filter results:*"
            )
            markup = build_quality_menu(cache_id)
        elif menu_type == "lang":
            menu_text = (
                f"🌐 **Select Language for:** `{search_query}`\n\n"
                f"✨ *Choose a language below to filter results:*"
            )
            markup = build_language_menu(cache_id)
        elif menu_type == "season":
            menu_text = (
                f"🎬 **Select Season for:** `{search_query}`\n\n"
                f"✨ *Choose a season below to filter results:*"
            )
            markup = build_season_menu(cache_id)
        else:
            await query.answer()
            return

        try:
            await query.message.edit_text(menu_text, reply_markup=markup)
        except MessageNotModified:
            pass
        await query.answer()

    elif data.startswith("flt_set_"):
        parts = data.split("_")
        cache_id = parts[2]
        filter_val = "_".join(parts[3:])
        base_query = SEARCH_CACHE.get(cache_id)

        if not base_query:
            await query.answer("⚠️ Search session expired. Please search again.", show_alert=True)
            return

        target_query = f"{base_query} {filter_val}".strip()
        files, total = await db.search_files(target_query, offset=0, limit=PAGE_SIZE)

        if total == 0:
            display_filter = "Season " + filter_val[1:] if filter_val.startswith("S") and filter_val[1:].isdigit() else filter_val
            await query.answer(f"❌ No {display_filter} results found for '{base_query}'", show_alert=True)
            return

        new_cache_id = uuid.uuid4().hex[:8]
        SEARCH_CACHE[new_cache_id] = target_query
        total_pages = math.ceil(total / PAGE_SIZE)
        markup = build_pagination_markup(files, new_cache_id, 1, total_pages)
        response_text = format_search_text(target_query, total, 1, total_pages)
        try:
            await query.message.edit_text(response_text, reply_markup=markup)
        except MessageNotModified:
            pass
        await query.answer(f"✅ Filter applied: {filter_val}")

    elif data.startswith("flt_back_"):
        parts = data.split("_")
        cache_id = parts[2]
        search_query = SEARCH_CACHE.get(cache_id)

        if not search_query:
            await query.answer("⚠️ Search session expired. Please search again.", show_alert=True)
            return

        files, total = await db.search_files(search_query, offset=0, limit=PAGE_SIZE)
        total_pages = math.ceil(total / PAGE_SIZE) if total > 0 else 1

        markup = build_pagination_markup(files, cache_id, 1, total_pages)
        response_text = format_search_text(search_query, total, 1, total_pages)
        try:
            await query.message.edit_text(response_text, reply_markup=markup)
        except MessageNotModified:
            pass
        await query.answer()

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

    elif data == "cb_disclaimer":
        disclaimer_text = (
            "⚖️ **Legal Disclaimer & Terms of Service**\n\n"
            "• **Non-Hosting Policy:** This bot does not store, host, upload, or reproduce any media files or video content on its servers.\n"
            "• **Telegram Indexing:** All media files and links provided are indexed automatically from publicly accessible third-party channels on Telegram.\n"
            "• **Copyright Compliance:** CineVerse respects intellectual property rights. If you are a copyright owner or authorized representative and wish to request removal of indexed files, please use the `/dmca` command to submit a takedown notice.\n\n"
            "⚡ **Powered By:** **CineVerse Network**"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="cb_home")]])
        try:
            await query.message.edit_caption(caption=disclaimer_text, reply_markup=markup)
        except Exception:
            await query.message.edit_text(disclaimer_text, reply_markup=markup)
        await query.answer()

    elif data == "cb_dmca":
        dmca_text = (
            "📋 **DMCA & Copyright Infringement Takedown Notice**\n\n"
            "If you are a copyright owner or an agent thereof and believe that any content indexed by CineVerse infringes upon your copyrights, please submit your request:\n\n"
            "📌 **How to submit:**\n"
            "Send `/dmca <Movie/Series Name, File Details, or Link>`\n\n"
            "**Example:**\n"
            "`/dmca Please remove the movie 'Movie Title (2024)' from index.`\n\n"
            "Your notice will be immediately forwarded to the bot administrator for prompt review and delisting."
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="cb_home")]])
        try:
            await query.message.edit_caption(caption=dmca_text, reply_markup=markup)
        except Exception:
            await query.message.edit_text(dmca_text, reply_markup=markup)
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
            f"⚖️ **Disclaimer:** *This bot does not store or host any files on its servers. All media files are indexed from third-party channels on Telegram. If you are a copyright owner and want to report/remove content, please use* `/dmca` *command.*\n\n"
            f"💬 *Type any movie name below to search immediately!*"
        )
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📖 Help", callback_data="cb_help"),
                InlineKeyboardButton("ℹ️ About", callback_data="cb_about")
            ],
            [
                InlineKeyboardButton("⚖️ Disclaimer", callback_data="cb_disclaimer"),
                InlineKeyboardButton("📋 DMCA", callback_data="cb_dmca")
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
        response_text = format_search_text(search_query, total, page, total_pages)
        try:
            await query.message.edit_text(response_text, reply_markup=markup)
        except MessageNotModified:
            pass
        await query.answer()

# ---------------------------------------------------------------------------
# BACKGROUND DUMMY HTTP SERVER (FOR RENDER / KOYEB / PAAS HEALTH CHECKS)
# ---------------------------------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK - CineVerse Movie Search Bot is running 24/7!\n")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        # Silence default access log spam from Render health probes
        pass

def start_health_server():
    port_str = os.getenv("PORT", "8080")
    try:
        port = int(port_str)
    except (ValueError, TypeError):
        port = 8080

    def run_server():
        try:
            server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
            logger.info(f"🌐 Render Health Check HTTP server listening on 0.0.0.0:{port}")
            server.serve_forever()
        except Exception as e:
            logger.warning(f"Could not start health check server on port {port}: {e}")

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

# ---------------------------------------------------------------------------
# START BOT VIA STANDARD app.run()
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    start_health_server()
    print("=" * 60)
    print("🚀 CineVerse Movie Search Bot is starting...")
    print(f"📌 Admin ID: {ADMIN_ID} | DB Channel: {DB_CHANNEL_ID}")
    print("=" * 60)
    app.run()

