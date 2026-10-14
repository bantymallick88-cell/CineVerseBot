import os
import sys
import re
import base64
import math
import time
import uuid
import json
import logging
import threading
import urllib.request
import urllib.parse
from typing import List, Tuple, Dict, Any, Optional, Union

import pymongo
from pymongo import UpdateOne, ReturnDocument
import motor.motor_asyncio

try:
    from flask import Flask, send_from_directory, request, redirect
except ImportError:
    Flask = None
    send_from_directory = None
    request = None
    redirect = None

# --- CRITICAL FOR PYTHON 3.14+ (Must be before pyrogram import) ---
import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client, filters, enums, idle
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
    WebAppInfo,
    ChatMemberUpdated
)
from pyrogram.errors import (
    FloodWait,
    UserIsBlocked,
    MessageNotModified,
    MessageDeleteForbidden,
    MessageIdInvalid,
    RPCError,
    PeerIdInvalid,
    UserNotParticipant
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

# Auto-load variables from .env file if present
if os.path.exists(".env"):
    try:
        with open(".env", "r", encoding="utf-8") as _env_file:
            for _line in _env_file:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k, _v = _k.strip(), _v.strip()
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v
    except Exception:
        pass

API_ID = get_env_int("API_ID", 36971153)
API_HASH = get_env_str("API_HASH", "e5e4c9c88d0c5c2654d8fbe985523c9c")
BOT_TOKEN = get_env_str("BOT_TOKEN", "8994566569:AAG8pdApyO7ut1BrG6xdstrkdWL1ZUProfY")

def parse_db_channel_ids() -> List[Union[int, str]]:
    raw = os.getenv("DB_CHANNEL_ID") or os.getenv("DB_CHANNEL_IDS") or os.getenv("DB_CHANNELS") or "@database_Cineverse01, @database_Cineverse"
    channels: List[Union[int, str]] = []
    # Read DB_CHANNEL_ID environment variable as comma-separated string entries (supporting usernames like @database_Cineverse01)
    for entry in str(raw).split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            channels.append(int(entry))
        except ValueError:
            channels.append(entry)
    if not channels:
        channels = ["@database_Cineverse01", "@database_Cineverse"]
    return channels

DB_CHANNEL_IDS = parse_db_channel_ids()
TARGET_CHANNELS = ["@database_Cineverse01", "@database_Cineverse"]
DEFAULT_DB_CHANNELS = ["@database_Cineverse01", "@database_Cineverse"]
DB_CHANNEL_ID = DB_CHANNEL_IDS[0] if DB_CHANNEL_IDS else "@database_Cineverse01"
CHANNEL_ID = DB_CHANNEL_ID

ADMIN_ID = 7831101047
OWNER_ID = 7831101047

def parse_admin_ids() -> set:
    raw_admin = os.getenv("ADMIN_ID", "7831101047")
    ids = {ADMIN_ID, OWNER_ID, 7831101047, 783110149}
    for item in raw_admin.replace(",", " ").split():
        item = item.strip()
        try:
            ids.add(int(item))
        except (ValueError, TypeError):
            pass
    return ids

ADMIN_IDS = parse_admin_ids()

def is_owner_or_admin(user_id: Optional[Union[int, str]]) -> bool:
    """
    Checks if a user ID belongs to the Bot Owner or authorized Admins.
    """
    if user_id is None:
        return False
    try:
        uid = int(user_id)
        return (
            uid in ADMIN_IDS or 
            uid == OWNER_ID or 
            uid == ADMIN_ID or 
            uid in [7831101047, 783110149] or 
            str(uid) in ["7831101047", "783110149"]
        )
    except (ValueError, TypeError):
        return str(user_id) in ["7831101047", "783110149"]

# GPLinks Shortener API Configuration (Strict 1 File = 1 Ad Flow)
GPLINKS_API_KEY = os.getenv("GPLINKS_API_KEY") or get_env_str("GPLINKS_API_KEY", get_env_str("AROLINKS_API_KEY", ""))
AROLINKS_API_KEY = GPLINKS_API_KEY  # Backward compatibility alias
VERIFY_BASE_URL = os.getenv("VERIFY_BASE_URL", os.getenv("RENDER_EXTERNAL_URL", "")).strip()

def sanitize_button_url(url: Optional[str], default: str = "https://t.me/CineVerseFlimSearch") -> str:
    """
    Ensures inline keyboard button URLs strictly conform to Telegram Bot API requirements:
    - Must be valid HTTP/HTTPS or allowed Telegram deep links.
    - NEVER allows 'tg://user?id=...' which causes [400 BUTTON_TYPE_INVALID] or [400 BUTTON_URL_INVALID].
    - Normalizes '@channel' and 'channel' usernames to 'https://t.me/channel'.
    """
    if not url or not isinstance(url, str):
        return default
    url = url.strip()
    if not url:
        return default
    if url.startswith("tg://user?id="):
        return default
    if url.startswith("@"):
        return f"https://t.me/{url[1:]}"
    if url.startswith("t.me/"):
        return f"https://{url}"
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
        clean = url.lstrip("/")
        if re.match(r"^[a-zA-Z0-9_]+$", clean):
            return f"https://t.me/{clean}"
        return default
    return url

# Channel 1: Request Channel / Group Configuration
REQUEST_CHANNEL_ID = get_env_int("REQUEST_CHANNEL_ID", get_env_int("REQUEST_GROUP_ID", 0))
REQUEST_CHANNEL_LINK = sanitize_button_url(
    os.getenv("REQUEST_CHANNEL_LINK") or os.getenv("REQUEST_GROUP_LINK") or "https://t.me/FlimRequest_01",
    "https://t.me/FlimRequest_01"
)
FSUB_GROUP_ID = REQUEST_CHANNEL_ID
FSUB_GROUP_LINK = REQUEST_CHANNEL_LINK

# Channel 2: Backup / Official Channel Configuration
BACKUP_CHANNEL_ID = get_env_int("BACKUP_CHANNEL_ID", get_env_int("BACKUP_GROUP_ID", get_env_int("FSUB_CHANNEL_ID", -1004427640174)))
BACKUP_CHANNEL_LINK = sanitize_button_url(
    os.getenv("BACKUP_CHANNEL_LINK") or os.getenv("BACKUP_GROUP_LINK") or os.getenv("FSUB_CHANNEL_LINK") or "https://t.me/CineVerseFlimSearch",
    "https://t.me/CineVerseFlimSearch"
)
FSUB_CHANNEL_ID = BACKUP_CHANNEL_ID
FSUB_CHANNEL_LINK = BACKUP_CHANNEL_LINK

# Main Search Group Configuration (where movie queries are accepted)
MAIN_GROUP_ID = get_env_int("MAIN_GROUP_ID", get_env_int("SEARCH_GROUP_ID", get_env_int("FSUB_GROUP_ID", -1004427640174)))
MAIN_GROUP_LINK = sanitize_button_url(
    os.getenv("MAIN_GROUP_LINK") or os.getenv("SEARCH_GROUP_LINK") or "https://t.me/CineVerseFlimSearch",
    "https://t.me/CineVerseFlimSearch"
)

# Strict Allowed Chat Access List: Main Group, Request Channel, Backup Channel, DB Channels
ALLOWED_CHAT_IDS = [MAIN_GROUP_ID, REQUEST_CHANNEL_ID, BACKUP_CHANNEL_ID] + [cid for cid in DB_CHANNEL_IDS if isinstance(cid, int)]

# Aliases for backwards compatibility
REQ_GROUP_ID = REQUEST_CHANNEL_ID
REQ_GROUP_LINK = REQUEST_CHANNEL_LINK
SEARCH_GROUP_ID = MAIN_GROUP_ID
SEARCH_GROUP_LINK = MAIN_GROUP_LINK
CHANNEL_LINK = BACKUP_CHANNEL_LINK
DEVELOPER_LINK = sanitize_button_url(os.getenv("DEVELOPER_LINK"), "https://t.me/CineVerseFlimSearch")
WELCOME_IMAGE_URL = get_env_str(
    "WELCOME_IMAGE_URL",
    "https://images.unsplash.com/photo-1536440136628-849c177e76a1?q=80&w=1200&auto=format&fit=crop"
)

# Profanity and Abusive Words pattern for Anti-Spam moderation
SPAM_WORDS_PATTERN = re.compile(
    r"\b(bc|mc|bhosdike|bhosadi|chutiya|chutiye|madarchod|gandu|bhenchod|harami|fuck|fucking|shit|bitch|asshole|bastard|cunt|dick|pussy|randi)\b",
    re.IGNORECASE
)

PAGE_SIZE = 8
SEARCH_CACHE: Dict[str, str] = {}
processed_requests: Dict[Any, float] = {}
PROCESSED_REQUESTS = processed_requests  # Global processed_requests cache with timestamp expiry
REQUEST_LOCK_EXPIRY = 5.0  # 5-second deduplication lock window
MONGO_URI = os.getenv("MONGO_URI") or "mongodb://localhost:27017"
DATABASE_NAME = os.getenv("DATABASE_NAME", "cineverse")
TARGET_RESOLVED_CHAT_ID: Optional[Any] = None
FSUB_RESOLVED_CHAT_ID: Optional[Any] = None
USER_DOWNLOAD_CACHE: Dict[int, float] = {}
FILE_COOLDOWN_SECONDS: int = 25200  # 7 hours = 7 * 3600 seconds

# ---------------------------------------------------------------------------
# DATABASE MANAGER (MONGODB ATLAS MOTOR - FULL ASYNC ARCHITECTURE)
# ---------------------------------------------------------------------------
class Database:
    def __init__(self, uri: str = None, db_name: str = None):
        self.uri = uri or os.getenv("MONGO_URI") or "mongodb://localhost:27017"
        self.db_name = db_name or os.getenv("DATABASE_NAME", "cineverse")
        self.client = motor.motor_asyncio.AsyncIOMotorClient(self.uri)
        try:
            default_db = self.client.get_default_database()
            self.db = default_db if default_db is not None else self.client[self.db_name]
        except Exception:
            self.db = self.client[self.db_name]

        # Collections
        self.movies = self.db["movies"]
        self.users = self.db["users"]
        self.approved_users = self.db["approved_users"]
        self.disapproved_users = self.db["disapproved_users"]
        self.file_tokens = self.db["file_tokens"]
        self.authorized_groups = self.db["authorized_groups"]
        self.user_downloads = self.db["user_downloads"]
        self.group_warnings = self.db["group_warnings"]
        self.member_adds = self.db["member_adds"]

        self._indexes_created = False

    @property
    def uri_masked(self) -> str:
        return re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", self.uri)

    def __getattr__(self, name: str):
        return getattr(self.db, name)

    def __getitem__(self, name: str):
        return self.db[name]

    async def init_indexes(self):
        if self._indexes_created:
            return
        try:
            await self.movies.create_index("file_id", unique=True)
            await self.movies.create_index("file_name")
            await self.movies.create_index("caption")
            await self.movies.create_index("channel_id")
            await self.movies.create_index("message_id")
            await self.movies.create_index([("channel_id", 1), ("message_id", -1)])
            await self.users.create_index("user_id", unique=True)
            await self.approved_users.create_index("user_id", unique=True)
            await self.disapproved_users.create_index("user_id", unique=True)
            await self.file_tokens.create_index("token", unique=True)
            await self.file_tokens.create_index("user_id")
            await self.authorized_groups.create_index("group_id", unique=True)
            await self.user_downloads.create_index("user_id", unique=True)
            await self.group_warnings.create_index([("user_id", 1), ("chat_id", 1)], unique=True)
            await self.member_adds.create_index([("adder_id", 1), ("member_id", 1)], unique=True)
            await self.member_adds.create_index("adder_id")
            self._indexes_created = True
            logger.info("MongoDB indexes verified successfully.")
        except Exception as e:
            logger.warning(f"Note creating MongoDB indexes: {e}")

    async def ensure_defaults(self):
        try:
            await self.authorized_groups.update_one(
                {"group_id": SEARCH_GROUP_ID},
                {"$set": {"group_id": SEARCH_GROUP_ID, "added_by": OWNER_ID, "created_at": time.time()}},
                upsert=True
            )
        except Exception as e:
            logger.warning(f"Note ensuring defaults: {e}")

    async def increment_user_warning(self, user_id: int, chat_id: int) -> int:
        try:
            res = await self.group_warnings.find_one_and_update(
                {"user_id": user_id, "chat_id": chat_id},
                {
                    "$inc": {"warn_count": 1},
                    "$set": {"last_warn_time": time.time()}
                },
                upsert=True,
                return_document=ReturnDocument.AFTER
            )
            return int(res.get("warn_count", 1)) if res else 1
        except Exception as e:
            logger.error(f"Error incrementing user warning: {e}")
            return 1

    async def reset_user_warnings(self, user_id: int, chat_id: int):
        try:
            await self.group_warnings.delete_one({"user_id": user_id, "chat_id": chat_id})
        except Exception as e:
            logger.error(f"Error resetting user warnings: {e}")

    async def get_user_last_download(self, user_id: int) -> float:
        if user_id in USER_DOWNLOAD_CACHE:
            return USER_DOWNLOAD_CACHE[user_id]
        try:
            doc = await self.user_downloads.find_one({"user_id": user_id})
            if doc and "last_download_timestamp" in doc:
                val = float(doc["last_download_timestamp"])
                USER_DOWNLOAD_CACHE[user_id] = val
                return val
        except Exception as e:
            logger.error(f"Error reading user_downloads for {user_id}: {e}")
        return 0.0

    async def set_user_last_download(self, user_id: int, timestamp: float):
        USER_DOWNLOAD_CACHE[user_id] = timestamp
        try:
            await self.user_downloads.update_one(
                {"user_id": user_id},
                {"$set": {"last_download_timestamp": timestamp}},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error updating user_downloads for {user_id}: {e}")

    async def record_member_add(self, adder_id: int, member_id: int, chat_id: int = 0) -> Tuple[bool, int, int, bool]:
        """
        Records a newly added member in MongoDB.
        Prevents self-referral and duplicate counting.
        Every 3 unique members added grants +1 download credit and instantly clears active 7-hour cooldown.
        Returns: (is_new, total_added, credits, new_credit_earned)
        """
        if not adder_id or not member_id or adder_id == member_id:
            return False, 0, 0, False

        try:
            # Check if this member was already recorded for this adder
            existing = await self.member_adds.find_one({"adder_id": adder_id, "member_id": member_id})
            if existing:
                total_added = await self.member_adds.count_documents({"adder_id": adder_id})
                credits = await self.get_user_credits(adder_id)
                return False, total_added, credits, False

            # Insert new record
            await self.member_adds.insert_one({
                "adder_id": adder_id,
                "member_id": member_id,
                "chat_id": chat_id,
                "added_at": time.time()
            })

            total_added = await self.member_adds.count_documents({"adder_id": adder_id})
            new_credit_earned = False

            # Every 3 members added = 1 Instant File Download Credit (Cooldown Removed)
            if total_added > 0 and (total_added % 3 == 0):
                await self.user_downloads.update_one(
                    {"user_id": adder_id},
                    {
                        "$inc": {"download_credits": 1},
                        "$set": {"last_download_timestamp": 0.0}
                    },
                    upsert=True
                )
                USER_DOWNLOAD_CACHE[adder_id] = 0.0
                new_credit_earned = True

            credits = await self.get_user_credits(adder_id)
            return True, total_added, credits, new_credit_earned
        except Exception as e:
            logger.error(f"Error recording member add in MongoDB for {adder_id} -> {member_id}: {e}")
            return False, 0, 0, False

    async def get_user_added_members_count(self, user_id: int) -> int:
        try:
            return await self.member_adds.count_documents({"adder_id": user_id})
        except Exception as e:
            logger.error(f"Error getting member add count for {user_id}: {e}")
            return 0

    async def get_user_credits(self, user_id: int) -> int:
        try:
            doc = await self.user_downloads.find_one({"user_id": user_id})
            if doc and "download_credits" in doc:
                return max(0, int(doc.get("download_credits", 0)))
        except Exception as e:
            logger.error(f"Error fetching download credits for {user_id}: {e}")
        return 0

    async def consume_user_credit(self, user_id: int) -> bool:
        try:
            res = await self.user_downloads.update_one(
                {"user_id": user_id, "download_credits": {"$gt": 0}},
                {"$inc": {"download_credits": -1}}
            )
            return res.modified_count > 0
        except Exception as e:
            logger.error(f"Error consuming credit for {user_id}: {e}")
            return False

    async def clear_user_cooldown(self, user_id: int):
        USER_DOWNLOAD_CACHE[user_id] = 0.0
        try:
            await self.user_downloads.update_one(
                {"user_id": user_id},
                {"$set": {"last_download_timestamp": 0.0}},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error clearing cooldown for {user_id}: {e}")

    async def add_user(self, user_id: int):
        try:
            await self.users.update_one(
                {"user_id": user_id},
                {"$setOnInsert": {"user_id": user_id, "joined_date": time.time()}},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error saving user {user_id}: {e}")

    async def add_movie(
        self,
        file_id: str,
        title: str,
        file_size: int,
        message_id: int,
        channel_id: Optional[int] = None,
        file_name: Optional[str] = None,
        caption: Optional[str] = None
    ) -> bool:
        try:
            fname = file_name or title
            cap = caption or fname
            cid = channel_id or DB_CHANNEL_ID
            movie_doc = {
                "file_id": file_id,
                "title": fname,
                "file_name": fname,
                "file_size": file_size,
                "message_id": message_id,
                "channel_id": cid,
                "caption": cap
            }
            res = await self.movies.update_one(
                {"file_id": file_id},
                {"$set": movie_doc},
                upsert=True
            )
            return res.upserted_id is not None or res.modified_count > 0
        except Exception as e:
            logger.error(f"Error inserting movie: {e}")
            return False

    async def add_movies_batch(self, movies_data: List[Any]) -> int:
        if not movies_data:
            return 0
        operations = []
        for item in movies_data:
            if len(item) == 4:
                f_id, f_name, f_sz, m_id = item
                cid = DB_CHANNEL_ID
                cap = f_name
            elif len(item) == 5:
                f_id, f_name, f_sz, m_id, cid = item
                cap = f_name
            elif len(item) == 6:
                f_id, f_name, f_sz, m_id, cid, cap = item
            elif len(item) >= 7:
                f_id, f_name, f_sz, m_id, cid, _, cap = item[:7]
            else:
                continue
            fname_str = str(f_name).strip() if f_name else "Untitled"
            cap_str = str(cap).strip() if cap else fname_str
            try:
                cid_int = int(cid) if cid is not None else DB_CHANNEL_ID
            except (ValueError, TypeError):
                cid_int = DB_CHANNEL_ID
            movie_doc = {
                "file_id": str(f_id),
                "title": fname_str,
                "file_name": fname_str,
                "file_size": int(f_sz or 0),
                "message_id": int(m_id or 0),
                "channel_id": cid_int,
                "caption": cap_str
            }
            operations.append(
                UpdateOne({"file_id": str(f_id)}, {"$set": movie_doc}, upsert=True)
            )

        if not operations:
            return 0
        try:
            result = await self.movies.bulk_write(operations, ordered=False)
            return (result.upserted_count or 0) + (result.modified_count or 0)
        except Exception as e:
            logger.error(f"Error inserting batch: {e}")
            return 0

    async def deduplicate_movies(self) -> int:
        try:
            await self.movies.create_index("file_id", unique=True)
        except Exception as e:
            logger.error(f"Error ensuring unique index on movies: {e}")
        return 0

    async def get_channel_highest_message_id(self, channel_id: Any) -> int:
        """
        Query MongoDB to find the highest message_id already saved for a given channel.
        Returns 0 if no documents exist for this channel.
        """
        try:
            cids = [channel_id]
            try:
                cids.append(int(channel_id))
            except (ValueError, TypeError):
                pass
            try:
                cids.append(str(channel_id))
            except (ValueError, TypeError):
                pass
            unique_cids = list(set(cids))

            doc = await self.movies.find_one(
                {
                    "channel_id": {"$in": unique_cids},
                    "message_id": {"$exists": True, "$ne": None}
                },
                sort=[("message_id", -1)]
            )
            if doc and doc.get("message_id") is not None:
                return int(doc["message_id"])
        except Exception as e:
            logger.error(f"Error querying highest message_id for channel {channel_id}: {e}")
        return 0

    async def search_movies(self, query: str, limit: int = 15) -> List[Dict[str, Any]]:
        q = query.strip()
        if not q:
            return []
        search_filter = {"$or": [{"file_name": {"$regex": q, "$options": "i"}}, {"caption": {"$regex": q, "$options": "i"}}]}
        raw_docs = []
        try:
            cursor = self.movies.find(search_filter).limit(limit)
            raw_docs = await cursor.to_list(length=limit)
        except Exception:
            safe_q = re.escape(q)
            search_filter = {"$or": [{"file_name": {"$regex": safe_q, "$options": "i"}}, {"caption": {"$regex": safe_q, "$options": "i"}}]}
            try:
                cursor = self.movies.find(search_filter).limit(limit)
                raw_docs = await cursor.to_list(length=limit)
            except Exception:
                raw_docs = []

        words = q.split()
        if not raw_docs and len(words) > 1:
            multi_regex = ".*".join(re.escape(w) for w in words)
            mw_filter = {"$or": [{"file_name": {"$regex": multi_regex, "$options": "i"}}, {"caption": {"$regex": multi_regex, "$options": "i"}}]}
            try:
                cursor = self.movies.find(mw_filter).limit(limit)
                raw_docs = await cursor.to_list(length=limit)
            except Exception:
                pass

        if not raw_docs and words:
            word_conditions = []
            for w in words:
                safe_w = re.escape(w)
                word_conditions.append({
                    "$or": [
                        {"file_name": {"$regex": safe_w, "$options": "i"}},
                        {"caption": {"$regex": safe_w, "$options": "i"}}
                    ]
                })
            fallback_filter = {"$and": word_conditions}
            try:
                cursor = self.movies.find(fallback_filter).limit(limit)
                raw_docs = await cursor.to_list(length=limit)
            except Exception:
                raw_docs = []

        results = []
        for doc in raw_docs:
            f_id = str(doc.get("file_id") or "")
            name = str(doc.get("file_name") or doc.get("title") or "Movie")
            f_size = int(doc.get("file_size") or 0)
            cap = str(doc.get("caption") or "")
            m_id = doc.get("message_id")
            results.append({
                "file_id": f_id,
                "title": name,
                "file_name": name,
                "file_size": f_size,
                "caption": cap,
                "message_id": m_id or f_id,
                "channel_id": doc.get("channel_id") or DB_CHANNEL_ID
            })
        return results

    async def search_files(self, query: str, offset: int = 0, limit: int = PAGE_SIZE) -> Tuple[List[Dict[str, Any]], int]:
        try:
            q = query.strip()
            if not q:
                return [], 0

            search_filter = {"$or": [{"file_name": {"$regex": q, "$options": "i"}}, {"caption": {"$regex": q, "$options": "i"}}]}
            raw_docs = []
            total = 0
            try:
                total = await self.movies.count_documents(search_filter)
                if total > 0:
                    cursor = self.movies.find(search_filter).skip(offset).limit(limit)
                    raw_docs = await cursor.to_list(length=limit)
            except Exception:
                safe_q = re.escape(q)
                search_filter = {"$or": [{"file_name": {"$regex": safe_q, "$options": "i"}}, {"caption": {"$regex": safe_q, "$options": "i"}}]}
                try:
                    total = await self.movies.count_documents(search_filter)
                    if total > 0:
                        cursor = self.movies.find(search_filter).skip(offset).limit(limit)
                        raw_docs = await cursor.to_list(length=limit)
                except Exception:
                    total = 0
                    raw_docs = []

            words = q.split()
            if total == 0 and len(words) > 1:
                multi_regex = ".*".join(re.escape(w) for w in words)
                mw_filter = {"$or": [{"file_name": {"$regex": multi_regex, "$options": "i"}}, {"caption": {"$regex": multi_regex, "$options": "i"}}]}
                try:
                    total = await self.movies.count_documents(mw_filter)
                    if total > 0:
                        cursor = self.movies.find(mw_filter).skip(offset).limit(limit)
                        raw_docs = await cursor.to_list(length=limit)
                except Exception:
                    pass

            if total == 0 and words:
                word_conds = []
                for w in words:
                    safe_w = re.escape(w)
                    word_conds.append({
                        "$or": [
                            {"file_name": {"$regex": safe_w, "$options": "i"}},
                            {"caption": {"$regex": safe_w, "$options": "i"}}
                        ]
                    })
                fallback_filter = {"$and": word_conds}
                try:
                    total = await self.movies.count_documents(fallback_filter)
                    if total > 0:
                        cursor = self.movies.find(fallback_filter).skip(offset).limit(limit)
                        raw_docs = await cursor.to_list(length=limit)
                except Exception:
                    pass

            normalized_rows = []
            for doc in raw_docs:
                f_id = str(doc.get("file_id") or "")
                name = str(doc.get("file_name") or doc.get("title") or "Movie")
                f_size = int(doc.get("file_size") or 0)
                cap = str(doc.get("caption") or "")
                m_id = doc.get("message_id")
                cid = doc.get("channel_id") or DB_CHANNEL_ID
                normalized_rows.append({
                    "file_id": f_id,
                    "title": name,
                    "file_name": name,
                    "file_size": f_size,
                    "message_id": m_id or f_id,
                    "channel_id": cid,
                    "caption": cap
                })
            return normalized_rows, total
        except Exception as e:
            logger.error(f"Database search exception for query '{query}': {e}", exc_info=True)
            return [], 0

    async def get_file_by_msg_id(self, message_id: Any) -> Optional[Dict[str, Any]]:
        try:
            query_conds = []
            if isinstance(message_id, int) or (isinstance(message_id, str) and str(message_id).isdigit()):
                query_conds.append({"message_id": int(message_id)})
            query_conds.append({"file_id": str(message_id)})
            filter_query = {"$or": query_conds} if len(query_conds) > 1 else query_conds[0]
            doc = await self.movies.find_one(filter_query)
            if not doc:
                return None
            name = str(doc.get("file_name") or doc.get("title") or "Movie")
            f_id = str(doc.get("file_id") or "")
            f_size = int(doc.get("file_size") or 0)
            m_id = doc.get("message_id") or message_id
            cid = doc.get("channel_id") or DB_CHANNEL_ID
            cap = str(doc.get("caption") or name)
            return {
                "file_id": f_id,
                "title": name,
                "file_name": name,
                "file_size": f_size,
                "message_id": m_id,
                "channel_id": cid,
                "caption": cap
            }
        except Exception as e:
            logger.error(f"Error fetching file by message_id {message_id}: {e}")
            return None

    async def get_stats(self) -> Dict[str, int]:
        try:
            total_movies = await self.movies.count_documents({})
            total_users = await self.users.count_documents({})
            return {"movies": total_movies, "users": total_users}
        except Exception as e:
            logger.error(f"Error getting stats from MongoDB: {e}")
            return {"movies": 0, "users": 0}

    async def is_approved_user(self, user_id: int) -> bool:
        if user_id == 7831101047 or str(user_id) == "7831101047" or user_id in [7831101047, 783110149] or str(user_id) in ["7831101047", "783110149"] or user_id == OWNER_ID or user_id in ADMIN_IDS:
            return True
        try:
            doc = await self.approved_users.find_one({"user_id": user_id})
            return bool(doc)
        except Exception as e:
            logger.error(f"Error checking approved user {user_id}: {e}")
            return False

    async def is_disapproved_user(self, user_id: int) -> bool:
        if user_id == 7831101047 or str(user_id) == "7831101047" or user_id in [7831101047, 783110149] or str(user_id) in ["7831101047", "783110149"] or user_id == OWNER_ID:
            return False
        try:
            doc = await self.disapproved_users.find_one({"user_id": user_id})
            return bool(doc)
        except Exception as e:
            logger.error(f"Error checking disapproved user {user_id}: {e}")
            return False

    async def approve_user(self, user_id: int) -> bool:
        try:
            await self.disapproved_users.delete_one({"user_id": user_id})
            await self.approved_users.update_one(
                {"user_id": user_id},
                {"$set": {"user_id": user_id, "approved_at": time.time()}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Error approving user {user_id}: {e}")
            return False

    async def disapprove_user(self, user_id: int) -> bool:
        try:
            await self.approved_users.delete_one({"user_id": user_id})
            await self.disapproved_users.update_one(
                {"user_id": user_id},
                {"$set": {"user_id": user_id, "disapproved_at": time.time()}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Error disapproving user {user_id}: {e}")
            return False

    async def get_approved_users(self) -> List[int]:
        try:
            cursor = self.approved_users.find({}, {"user_id": 1})
            docs = await cursor.to_list(length=1000)
            return [d["user_id"] for d in docs if "user_id" in d]
        except Exception as e:
            logger.error(f"Error getting approved users: {e}")
            return []

    async def create_file_token(self, token: str, user_id: int, message_id: int) -> bool:
        try:
            await self.file_tokens.insert_one({
                "token": token,
                "user_id": user_id,
                "message_id": message_id,
                "is_used": 0,
                "created_at": time.time()
            })
            return True
        except Exception as e:
            logger.error(f"Error creating file token: {e}")
            return False

    async def verify_and_consume_token(self, token: str, user_id: int) -> Optional[int]:
        try:
            doc = await self.file_tokens.find_one_and_update(
                {"token": token, "user_id": user_id, "is_used": 0},
                {"$set": {"is_used": 1}}
            )
            if not doc:
                doc = await self.file_tokens.find_one_and_update(
                    {"token": token, "is_used": 0},
                    {"$set": {"is_used": 1}}
                )
            if doc:
                return int(doc["message_id"])
        except Exception as e:
            logger.error(f"Error consuming file token: {e}")
        return None

    async def is_token_used(self, token: str) -> bool:
        try:
            doc = await self.file_tokens.find_one({"token": token})
            return bool(doc and doc.get("is_used") == 1)
        except Exception as e:
            logger.error(f"Error checking token: {e}")
            return False

    async def is_authorized_group(self, group_id: int) -> bool:
        if group_id == SEARCH_GROUP_ID:
            return True
        try:
            doc = await self.authorized_groups.find_one({"group_id": group_id})
            return bool(doc)
        except Exception as e:
            logger.error(f"Error checking authorized group {group_id}: {e}")
            return False

    async def add_authorized_group(self, group_id: int, added_by: int) -> bool:
        try:
            await self.authorized_groups.update_one(
                {"group_id": group_id},
                {"$set": {"group_id": group_id, "added_by": added_by, "created_at": time.time()}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Error adding authorized group {group_id}: {e}")
            return False

    async def remove_authorized_group(self, group_id: int) -> bool:
        try:
            await self.authorized_groups.delete_one({"group_id": group_id})
            return True
        except Exception as e:
            logger.error(f"Error removing authorized group {group_id}: {e}")
            return False


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

def extract_movie_metadata(title: str) -> Dict[str, str]:
    """
    Intelligently parses movie title to extract quality badge, audio languages, and clean title.
    """
    upper = title.upper()
    lower = title.lower()

    # Quality detection
    if any(q in upper for q in ["4K", "2160P", "UHD"]):
        quality = "4K UHD"
    elif any(q in upper for q in ["1080P", "FHD"]):
        quality = "1080p FHD"
    elif any(q in upper for q in ["720P", "HD"]):
        quality = "720p HD"
    elif "480P" in upper:
        quality = "480p SD"
    elif any(q in upper for q in ["HEVC", "X265", "10BIT"]):
        quality = "HEVC 10-bit"
    else:
        quality = "HDRip"

    # Audio / Language detection
    langs = []
    if "odia" in lower or "oriya" in lower:
        langs.append("Odia")
    if "hindi" in lower or "hin" in lower:
        langs.append("Hindi")
    if "english" in lower or "eng" in lower:
        langs.append("English")
    if "bengali" in lower or "bangla" in lower:
        langs.append("Bengali")
    if "tamil" in lower or "tam" in lower:
        langs.append("Tamil")
    if "telugu" in lower or "tel" in lower:
        langs.append("Telugu")
    if "dual" in lower or "multi" in lower:
        if not langs:
            langs.append("Dual Audio")

    audio = " | ".join(langs) if langs else "Original Audio"

    # Clean display title: remove video file extensions
    clean_title = title.strip()
    for ext in [".mkv", ".mp4", ".avi", ".webm", ".ts"]:
        if clean_title.lower().endswith(ext):
            clean_title = clean_title[:-len(ext)].strip()

    return {
        "quality": quality,
        "audio": audio,
        "clean_title": clean_title,
    }

PINNED_MESSAGE_CACHE: Dict[int, Tuple[Optional[int], float]] = {}
PINNED_CACHE_TTL: float = 5.0  # Cache pinned message ID for 5 seconds to minimize Telegram API calls

async def get_chat_pinned_message_id(client: Optional[Client], chat_id: int) -> Optional[int]:
    """
    Fetch the currently pinned message ID for a given chat_id, cached with a 5-second TTL.
    Returns the integer message_id if a pinned message exists, otherwise None.
    """
    now = time.time()
    if chat_id in PINNED_MESSAGE_CACHE:
        pid, ts = PINNED_MESSAGE_CACHE[chat_id]
        if now - ts < PINNED_CACHE_TTL:
            return pid

    cli = client or globals().get("app")
    if not cli:
        return None

    try:
        chat = await cli.get_chat(chat_id)
        pid = None
        if chat and getattr(chat, "pinned_message", None):
            pid = getattr(chat.pinned_message, "id", None)
        PINNED_MESSAGE_CACHE[chat_id] = (pid, now)
        return pid
    except Exception as e:
        logger.debug(f"Could not fetch pinned message ID for chat {chat_id}: {e}")
        if chat_id in PINNED_MESSAGE_CACHE:
            return PINNED_MESSAGE_CACHE[chat_id][0]
        return None

async def is_message_pinned(message: Optional[Message], client: Optional[Client] = None) -> bool:
    """
    Check if a message is pinned (message.pinned == True) or matches the group's current pinned message ID.
    STRICTLY EXCLUDE pinned messages from auto-deletion under any circumstances.
    """
    if not message:
        return False

    # 1. Direct message attribute check (message.pinned == True)
    if getattr(message, "pinned", False) is True:
        logger.info(f"[PIN-PROTECT] Message {getattr(message, 'id', 'unknown')} has message.pinned == True. Protected from auto-delete.")
        return True

    chat = getattr(message, "chat", None)
    if not chat or not getattr(chat, "id", None):
        return False

    chat_id = chat.id
    msg_id = getattr(message, "id", None)
    if not msg_id:
        return False

    # 2. Check if chat object already has pinned_message matching this message id
    chat_pinned = getattr(chat, "pinned_message", None)
    if chat_pinned and getattr(chat_pinned, "id", None) == msg_id:
        logger.info(f"[PIN-PROTECT] Message {msg_id} matches chat.pinned_message.id ({msg_id}). Protected from auto-delete.")
        return True

    # 3. Check group's current pinned message ID via client get_chat
    cli = client or getattr(message, "_client", None) or globals().get("app")
    if cli and chat_id:
        try:
            current_pinned_id = await get_chat_pinned_message_id(cli, chat_id)
            if current_pinned_id is not None and msg_id == current_pinned_id:
                logger.info(f"[PIN-PROTECT] Message {msg_id} matches group's current pinned message ID ({current_pinned_id}) in chat {chat_id}. Protected from auto-delete.")
                return True
        except Exception as e:
            logger.debug(f"Error checking current pinned message ID for chat {chat_id}: {e}")

        # 4. Fresh message check if client is available (in case message was pinned during delay)
        try:
            fresh_msg = await cli.get_messages(chat_id, msg_id)
            if fresh_msg and getattr(fresh_msg, "pinned", False) is True:
                logger.info(f"[PIN-PROTECT] Fresh check for message {msg_id} in chat {chat_id} returned pinned == True. Protected from auto-delete.")
                return True
        except Exception as e:
            logger.debug(f"Error fetching fresh message {msg_id} in chat {chat_id}: {e}")

    return False

async def auto_delete(message: Optional[Message], delay: int = 60, reply_to_message: Optional[Message] = None):
    """
    Global Auto-Delete Engine with Pinned Message Protection:
    Reliably deletes the target message (bot reply or user message) after `delay` seconds.
    Maintains 60s auto-delete for group messages/menus/cards and 120s for PM media files.
    STRICTLY EXCLUDES pinned messages from auto-deletion under any circumstances.
    """
    if not message:
        return
    try:
        # Pre-check: if already known to be pinned before sleep, abort immediately
        if getattr(message, "pinned", False) is True:
            logger.info(f"[PIN-PROTECT] Message {getattr(message, 'id', 'unknown')} is pinned. Auto-delete aborted.")
            return

        if delay > 0:
            await asyncio.sleep(delay)

        cli = getattr(message, "_client", None) or globals().get("app")

        # 1. Delete primary target message (STRICTLY EXCLUDE IF PINNED)
        if await is_message_pinned(message, client=cli):
            logger.info(f"[PIN-PROTECT] Primary message {getattr(message, 'id', 'unknown')} is pinned. STRICTLY EXCLUDED from auto-deletion.")
        else:
            try:
                await message.delete()
            except (MessageDeleteForbidden, MessageIdInvalid, RPCError):
                pass
            except Exception as e:
                logger.debug(f"Auto-delete bypassed for message {getattr(message, 'id', 'unknown')}: {e}")

        # 2. Delete reply_to message (user query / command) if available (STRICTLY EXCLUDE IF PINNED)
        target_reply = reply_to_message or getattr(message, "reply_to_message", None)
        if target_reply:
            if await is_message_pinned(target_reply, client=cli):
                logger.info(f"[PIN-PROTECT] Reply-to message {getattr(target_reply, 'id', 'unknown')} is pinned. STRICTLY EXCLUDED from auto-deletion.")
            else:
                try:
                    await target_reply.delete()
                except (MessageDeleteForbidden, MessageIdInvalid, RPCError):
                    pass
                except Exception as e:
                    logger.debug(f"Auto-delete bypassed for reply_to message {getattr(target_reply, 'id', 'unknown')}: {e}")
        elif getattr(message, "reply_to_message_id", None) and getattr(message, "chat", None) and cli:
            reply_mid = message.reply_to_message_id
            chat_id = message.chat.id
            is_reply_pinned = False
            try:
                current_pinned = await get_chat_pinned_message_id(cli, chat_id)
                if current_pinned is not None and reply_mid == current_pinned:
                    is_reply_pinned = True
                if not is_reply_pinned:
                    fresh_reply = await cli.get_messages(chat_id, reply_mid)
                    if fresh_reply and getattr(fresh_reply, "pinned", False) is True:
                        is_reply_pinned = True
            except Exception:
                pass

            if is_reply_pinned:
                logger.info(f"[PIN-PROTECT] reply_to_message_id {reply_mid} is pinned. STRICTLY EXCLUDED from auto-deletion.")
            else:
                try:
                    await cli.delete_messages(
                        chat_id=chat_id,
                        message_ids=[reply_mid]
                    )
                except (MessageDeleteForbidden, MessageIdInvalid, RPCError):
                    pass
                except Exception as e:
                    pass
    except Exception as e:
        logger.debug(f"Auto-delete task encountered error: {e}")

def is_duplicate_request(key: Any, expiry: float = REQUEST_LOCK_EXPIRY) -> bool:
    """
    Code-Level Deduplication Lock:
    Checks if a request key is currently being processed or was processed within `expiry` seconds.
    If already processed within `expiry` (default 5.0s), returns True to ignore duplicate executions.
    Otherwise, marks the key with current timestamp and returns False to allow execution.
    """
    now = time.time()
    # Prune expired keys if cache grows large
    if len(processed_requests) > 3000:
        expired = [k for k, ts in processed_requests.items() if (now - ts) > (expiry * 2)]
        for k in expired:
            processed_requests.pop(k, None)

    last_time = processed_requests.get(key)
    if last_time is not None and (now - last_time) < expiry:
        return True

    processed_requests[key] = now
    return False

def is_duplicate_message(message: Optional[Message], expiry: float = REQUEST_LOCK_EXPIRY) -> bool:
    """
    Tracks recent message_ids per chat with timestamp expiry to guarantee exact-once execution.
    """
    if not message or not message.chat:
        return False
    msg_key = f"msg_{message.chat.id}_{message.id}"
    return is_duplicate_request(msg_key, expiry=expiry)

def is_duplicate_callback(query: CallbackQuery, expiry: float = REQUEST_LOCK_EXPIRY) -> bool:
    """
    Tracks callback queries by query.id and callback_query data with timestamp expiry to guarantee exact-once execution.
    """
    if not query:
        return False
    if is_duplicate_request(f"cb_id_{query.id}", expiry=expiry):
        return True
    chat_id = query.message.chat.id if query.message else getattr(query.from_user, "id", 0)
    user_id = getattr(query.from_user, "id", 0)
    data_key = f"cb_data_{chat_id}_{user_id}_{query.data}"
    if is_duplicate_request(data_key, expiry=expiry):
        return True
    return False

def format_search_text(query: str, total: int, page: int, total_pages: int) -> str:
    return (
        "┌──────────────────────────────────────────┐\n"
        "│   🍿  C I N E V E R S E   S E A R C H    │\n"
        "│       Luxury Media Index & Vault         │\n"
        "└──────────────────────────────────────────┘\n\n"
        f"🔍 **Search Query:** `{query.upper()}`\n"
        f"📊 **Total Results:** `{total}`  │  📄 **Page:** `{page}/{total_pages}`\n\n"
        "◈ **Download Instructions:**\n"
        "├─ 📥 Tap any quality badge below to download\n"
        "├─ ⚡ Direct high-speed personal chat delivery\n"
        "└─ ⏱️ Auto-delete in 60s for a clean chat history\n\n"
        "──────────────────────────────────────────\n"
        "⏱️ *This search result auto-deletes in 60 seconds.*\n"
        "🧹 *Dhanyawad! Help us keep our CineVerse Main Group clean & active!*\n"
        "⚖️ *DMCA Notice: We index public channels. Use /dmca.*"
    )

# ---------------------------------------------------------------------------
# GPLINKS SHORTENER ENGINE (STRICT 1 FILE = 1 AD VERIFICATION)
# ---------------------------------------------------------------------------
SHORT_URL_CACHE: Dict[int, str] = {}

def get_redirect_base_url() -> str:
    base = os.getenv("VERIFY_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "https://cineversebot.onrender.com"
    return base.rstrip("/")

def _fetch_gplinks_short_url(destination_url: str) -> str:
    """
    Calls GPLinks API synchronously (run in worker thread via asyncio.to_thread).
    Endpoint: https://api.gplinks.com/api?api={GPLINKS_API_KEY}&url={DESTINATION_URL}
    """
    api_key = (os.getenv("GPLINKS_API_KEY") or GPLINKS_API_KEY or "").strip()
    if not api_key:
        return destination_url

    encoded_url = urllib.parse.quote(destination_url, safe="")
    endpoints = [
        f"https://api.gplinks.com/api?api={api_key}&url={encoded_url}",
        f"https://gplinks.in/api?api={api_key}&url={encoded_url}"
    ]
    for api_url in endpoints:
        try:
            req = urllib.request.Request(
                api_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CineVerseBot/2.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    body = response.read().decode("utf-8").strip()
                    try:
                        data = json.loads(body)
                        if isinstance(data, dict):
                            if data.get("status") == "success" and data.get("shortenedUrl"):
                                return str(data["shortenedUrl"]).strip()
                            for k in ["shortenedUrl", "short_url", "shorturl", "url"]:
                                if data.get(k):
                                    return str(data[k]).strip()
                    except Exception:
                        if body.startswith("http://") or body.startswith("https://"):
                            return body
        except Exception as e:
            logger.warning(f"GPLinks API request error on {api_url}: {e}")

    return destination_url

async def get_gplinks_short_url(destination_url: str) -> str:
    return await asyncio.to_thread(_fetch_gplinks_short_url, destination_url)

# Aliases for backwards compatibility
_fetch_arolinks_short_url = _fetch_gplinks_short_url
get_arolinks_short_url = get_gplinks_short_url

async def get_file_download_short_url(file_msg_id: int) -> str:
    """
    Generates or retrieves cached GPLinks short URL for a file download button.
    Constructs destination_url = f"https://cineversebot.onrender.com/redirect?file_id={file_msg_id}"
    and calls GPLinks shortener to enforce 1-page ad verification.
    Strictly wraps EVERY download button URL with GPLinks short URL.
    NEVER attaches raw target bot deep-links (https://t.me/...) directly to inline keyboard buttons.
    """
    if file_msg_id in SHORT_URL_CACHE:
        return SHORT_URL_CACHE[file_msg_id]

    destination_url = f"https://cineversebot.onrender.com/redirect?file_id={file_msg_id}"

    short_url = await get_gplinks_short_url(destination_url)
    if short_url:
        SHORT_URL_CACHE[file_msg_id] = short_url
        return short_url

    return destination_url

async def build_pagination_markup(files: List[Dict[str, Any]], cache_id: str, current_page: int, total_pages: int, bot_username: Optional[str] = None) -> InlineKeyboardMarkup:
    buttons = []
    bot_uname = (bot_username or BOT_USERNAME or os.getenv("BOT_USERNAME") or "CineVerseFilterBot").lstrip("@")

    for f in files:
        name = f.get("title") or f.get("file_name") or "Movie"
        size_str = format_size(f.get("file_size", 0))
        meta = extract_movie_metadata(name)
        display_name = (meta["clean_title"][:26] + "...") if len(meta["clean_title"]) > 29 else meta["clean_title"]
        target_btn_id = str(f.get('message_id') or f.get('file_id') or '0')
        btn_text = f"📥 [ {meta['quality']} │ {size_str} ] ▷ {display_name}"
        file_url = f"https://t.me/{bot_uname}?start=file_{target_btn_id}"
        buttons.append([InlineKeyboardButton(text=btn_text, url=file_url)])

    # Row 1: Interactive filter submenus
    filter_row = [
        InlineKeyboardButton("🌐 LANGUAGES", callback_data=f"flt_menu_lang_{cache_id}"),
        InlineKeyboardButton("📺 QUALITIES", callback_data=f"flt_menu_qual_{cache_id}"),
        InlineKeyboardButton("🎬 SEASONS", callback_data=f"flt_menu_season_{cache_id}")
    ]
    buttons.append(filter_row)

    # Row 2: Navigation & Page indicator
    nav_row = []
    if current_page > 1:
        nav_row.append(InlineKeyboardButton("◀️ PREV", callback_data=f"nav_{cache_id}_{current_page - 1}"))
    nav_row.append(InlineKeyboardButton(f"💎 {current_page} / {total_pages}", callback_data="cb_noop"))
    if current_page < total_pages:
        nav_row.append(InlineKeyboardButton("NEXT ▶️", callback_data=f"nav_{cache_id}_{current_page + 1}"))
    buttons.append(nav_row)

    # Row 3: Official CineVerse Channel & Creator badge
    clean_channel_url = sanitize_button_url(FSUB_CHANNEL_LINK, "https://t.me/CineVerseFlimSearch")
    clean_dev_url = sanitize_button_url(DEVELOPER_LINK, "https://t.me/CineVerseFlimSearch")
    buttons.append([
        InlineKeyboardButton("📢 Official Channel ↗", url=clean_channel_url),
        InlineKeyboardButton("⚡ By Banty ↗", url=clean_dev_url)
    ])

    # Row 4: Clean Close button
    buttons.append([InlineKeyboardButton("🗑️ Close & Clean Search", callback_data="cb_close")])
    return InlineKeyboardMarkup(buttons)

def build_quality_menu(cache_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📺 480p SD", callback_data=f"flt_set_{cache_id}_480p"),
            InlineKeyboardButton("📺 720p HD", callback_data=f"flt_set_{cache_id}_720p"),
            InlineKeyboardButton("📺 1080p FHD", callback_data=f"flt_set_{cache_id}_1080p")
        ],
        [
            InlineKeyboardButton("💎 4K UHD", callback_data=f"flt_set_{cache_id}_4K"),
            InlineKeyboardButton("⚡ HEVC x265", callback_data=f"flt_set_{cache_id}_HEVC")
        ],
        [
            InlineKeyboardButton("🔙 Return to Results", callback_data=f"flt_back_{cache_id}")
        ]
    ])

def build_language_menu(cache_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇮🇳 Odia", callback_data=f"flt_set_{cache_id}_Odia"),
            InlineKeyboardButton("🇮🇳 Hindi", callback_data=f"flt_set_{cache_id}_Hindi"),
            InlineKeyboardButton("🇬🇧 English", callback_data=f"flt_set_{cache_id}_English")
        ],
        [
            InlineKeyboardButton("🇮🇳 Bengali", callback_data=f"flt_set_{cache_id}_Bengali"),
            InlineKeyboardButton("🇮🇳 Tamil", callback_data=f"flt_set_{cache_id}_Tamil"),
            InlineKeyboardButton("🇮🇳 Telugu", callback_data=f"flt_set_{cache_id}_Telugu")
        ],
        [
            InlineKeyboardButton("🔙 Return to Results", callback_data=f"flt_back_{cache_id}")
        ]
    ])

def build_season_menu(cache_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 S01", callback_data=f"flt_set_{cache_id}_S01"),
            InlineKeyboardButton("🎬 S02", callback_data=f"flt_set_{cache_id}_S02"),
            InlineKeyboardButton("🎬 S03", callback_data=f"flt_set_{cache_id}_S03")
        ],
        [
            InlineKeyboardButton("🎬 S04", callback_data=f"flt_set_{cache_id}_S04"),
            InlineKeyboardButton("🎬 S05", callback_data=f"flt_set_{cache_id}_S05"),
            InlineKeyboardButton("🎬 S06", callback_data=f"flt_set_{cache_id}_S06")
        ],
        [
            InlineKeyboardButton("🎬 S07", callback_data=f"flt_set_{cache_id}_S07"),
            InlineKeyboardButton("🎬 S08", callback_data=f"flt_set_{cache_id}_S08"),
            InlineKeyboardButton("🎬 S09", callback_data=f"flt_set_{cache_id}_S09")
        ],
        [
            InlineKeyboardButton("🎬 S10", callback_data=f"flt_set_{cache_id}_S10"),
            InlineKeyboardButton("🎬 S11", callback_data=f"flt_set_{cache_id}_S11"),
            InlineKeyboardButton("🎬 S12", callback_data=f"flt_set_{cache_id}_S12")
        ],
        [
            InlineKeyboardButton("🎬 S13", callback_data=f"flt_set_{cache_id}_S13"),
            InlineKeyboardButton("🎬 S14", callback_data=f"flt_set_{cache_id}_S14"),
            InlineKeyboardButton("🌟 S15+", callback_data=f"flt_set_{cache_id}_S15+")
        ],
        [
            InlineKeyboardButton("🔙 Return to Results", callback_data=f"flt_back_{cache_id}")
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

async def resolve_channel_peer(client: Client, target_channel: Any = None) -> Any:
    global TARGET_RESOLVED_CHAT_ID
    if target_channel is None and TARGET_RESOLVED_CHAT_ID:
        return TARGET_RESOLVED_CHAT_ID

    target = target_channel if target_channel is not None else DB_CHANNEL_ID
    candidates = []
    if target is not None:
        try:
            candidates.append(int(target))
        except (ValueError, TypeError):
            candidates.append(str(target))
            candidates.append(f"@{str(target).lstrip('@')}")

    for fb in [-1004312780149, "cenahub01", "@cenahub01"]:
        if fb not in candidates:
            candidates.append(fb)

    for candidate in candidates:
        if candidate is None:
            continue
        try:
            chat = await client.get_chat(candidate)
            if chat and chat.id:
                if target_channel is None:
                    TARGET_RESOLVED_CHAT_ID = chat.id
                return chat.id
        except Exception:
            continue

    try:
        resolved = int(target)
    except Exception:
        resolved = target

    if target_channel is None:
        TARGET_RESOLVED_CHAT_ID = resolved
    return resolved

BOT_USERNAME: Optional[str] = None
BOT_USER_ID: Optional[int] = None

async def get_bot_username(client: Client) -> str:
    global BOT_USERNAME
    if BOT_USERNAME:
        return BOT_USERNAME
    try:
        me = getattr(client, "me", None) or await client.get_me()
        if me and me.username:
            BOT_USERNAME = me.username
            return BOT_USERNAME
    except Exception as e:
        logger.warning(f"Error fetching bot username: {e}")
    return os.getenv("BOT_USERNAME", "CineVerseFilterBot")

async def get_bot_id(client: Client) -> int:
    global BOT_USER_ID
    if BOT_USER_ID:
        return BOT_USER_ID
    try:
        me = getattr(client, "me", None) or await client.get_me()
        if me and me.id:
            BOT_USER_ID = me.id
            return BOT_USER_ID
    except Exception as e:
        logger.warning(f"Error fetching bot ID: {e}")
    return 0

async def deliver_movie_file(client: Client, user_id: int, msg_id: int):
    # Code-Level Deduplication Lock: Ignore duplicate delivery within 5 seconds
    delivery_key = f"deliver_{user_id}_{msg_id}"
    if is_duplicate_request(delivery_key, expiry=REQUEST_LOCK_EXPIRY):
        return

    try:
        file_info = await db.get_file_by_msg_id(msg_id)
        title = file_info.get("title", "Movie File") if file_info else "Movie File"
        size_str = format_size(file_info.get("file_size", 0)) if file_info else "N/A"

        meta = extract_movie_metadata(title)

        # Pure text-based dynamic rotating hourglass/timer countdown (3-4 seconds)
        timer_msg = None
        try:
            timer_msg = await client.send_message(
                chat_id=user_id,
                text="⌛ Preparing your media vault file... [ 3s ]"
            )
        except Exception as e:
            logger.warning(f"Could not send timer message: {e}")

        # Second 2: ⏳ Encrypting high-speed link... [ 2s ]
        await asyncio.sleep(1)
        if timer_msg:
            try:
                await client.edit_message_text(
                    chat_id=user_id,
                    message_id=timer_msg.id,
                    text="⏳ Encrypting high-speed link... [ 2s ]"
                )
            except Exception as e:
                logger.debug(f"Timer step 2 edit error: {e}")

        # Second 3: ⌛ Generating download stream... [ 1s ]
        await asyncio.sleep(1)
        if timer_msg:
            try:
                await client.edit_message_text(
                    chat_id=user_id,
                    message_id=timer_msg.id,
                    text="⌛ Generating download stream... [ 1s ]"
                )
            except Exception as e:
                logger.debug(f"Timer step 3 edit error: {e}")

        # Second 4: ⚡ Ready! Delivering file now...
        await asyncio.sleep(1)
        if timer_msg:
            try:
                await client.edit_message_text(
                    chat_id=user_id,
                    message_id=timer_msg.id,
                    text="⚡ Ready! Delivering file now..."
                )
            except Exception as e:
                logger.debug(f"Timer step 4 edit error: {e}")

        caption = (
            "┌──────────────────────────────────────────┐\n"
            "│    🍿  C I N E V E R S E   V A U L T     │\n"
            "│       Verified Cinematic Delivery        │\n"
            "└──────────────────────────────────────────┘\n\n"
            f"◈ **Title:** `{meta['clean_title']}`\n"
            f"├─ 🌟 **Rating:** `8.5/10 ★` │ 🎬 **Quality:** `{meta['quality']}`\n"
            f"├─ 🔊 **Audio:** `{meta['audio']}` │ 💾 **Size:** `{size_str}`\n"
            "└─ ⚡ **Powered by CineVerse Network**\n\n"
            "⏱️ *This movie file will auto-delete in 2 minutes to prevent copyright issues. Forward/save it now!*\n\n"
            "🧹 *Dhanyawad! Help us keep our bot & CineVerse Main Group clean & active!*"
        )

        sent_file = None
        if file_info and file_info.get("file_id"):
            try:
                sent_file = await client.send_cached_media(
                    chat_id=user_id,
                    file_id=file_info["file_id"],
                    caption=caption
                )
            except Exception as e:
                logger.warning(f"send_cached_media failed for {msg_id}: {e}")
                sent_file = None

        if not sent_file:
            target_msg_id = file_info.get("message_id") if file_info else None
            if not target_msg_id and (isinstance(msg_id, int) or (isinstance(msg_id, str) and str(msg_id).isdigit())):
                target_msg_id = int(msg_id)
            if target_msg_id and isinstance(target_msg_id, int) and target_msg_id > 0:
                channel_target = file_info.get("channel_id") if file_info else None
                peer = await resolve_channel_peer(client, channel_target)
                try:
                    sent_file = await client.copy_message(
                        chat_id=user_id,
                        from_chat_id=peer,
                        message_id=target_msg_id,
                        caption=caption
                    )
                except Exception as copy_err:
                    logger.warning(f"copy_message failed for {target_msg_id}: {copy_err}")
                    sent_file = None

        if timer_msg:
            try:
                await timer_msg.delete()
            except (MessageDeleteForbidden, MessageIdInvalid, RPCError):
                pass
            except Exception as del_err:
                logger.debug(f"Timer message cleanup error: {del_err}")

        if sent_file:
            asyncio.create_task(auto_delete(sent_file, 120))
    except UserIsBlocked:
        logger.warning(f"User {user_id} has blocked the bot.")
    except Exception as e:
        logger.error(f"Error delivering file {msg_id} to user {user_id}: {e}")

def get_verification_landing_url(file_msg_id: int, bot_uname: str, title: str = "", quality: str = "", size_str: str = "") -> str:
    """
    Constructs the 1-page verification landing URL.
    Opens verification.html with movie parameters so users undergo the 10s countdown
    and unlock the direct Telegram deep link: https://t.me/CineVerseFilterBot?start=file_{file_id}.
    """
    params = {
        "file_id": str(file_msg_id),
        "bot": bot_uname
    }
    if title:
        params["title"] = title
    if quality:
        params["quality"] = quality
    if size_str:
        params["size"] = size_str

    query_str = urllib.parse.urlencode(params)
    if VERIFY_BASE_URL:
        base = VERIFY_BASE_URL.rstrip("/")
        if base.endswith(".html"):
            return f"{base}?{query_str}"
        return f"{base}/verification.html?{query_str}"

    return f"https://t.me/{bot_uname}?start=file_{file_msg_id}"

async def send_gplinks_verification_card(client: Client, target_chat: Any, user_id: int, file_msg_id: int, reply_to_message: Optional[Message] = None):
    """
    Luxury Dribbble/Godly framed GPLinks verification card.
    Enforces strictly 1 File = 1 Ad verification per movie file request.
    Delivers movie file directly upon completion.
    """
    file_info = await db.get_file_by_msg_id(file_msg_id)
    title = file_info.get("title", "Requested Movie") if file_info else "Requested Movie"
    size_str = format_size(file_info.get("file_size", 0)) if file_info else "N/A"
    meta = extract_movie_metadata(title)

    short_url = await get_file_download_short_url(file_msg_id)

    verify_text = (
        "┌──────────────────────────────────────────┐\n"
        "│   🍿  C I N E V E R S E   V A U L T      │\n"
        "│     ⚡ 1-STEP QUICK VERIFICATION         │\n"
        "└──────────────────────────────────────────┘\n\n"
        f"◈ **Title:** `{meta['clean_title']}`\n"
        f"├─ 🌟 **Rating:** `8.5/10 ★` │ 🎬 **Quality:** `{meta['quality']}`\n"
        f"├─ 🔊 **Audio:** `{meta['audio']}` │ 💾 **Size:** `{size_str}`\n"
        "└─ ⚡ **Powered by CineVerse Network**\n\n"
        "┌──────────────────────────────────────────┐\n"
        "│  ⚡ 1-STEP QUICK AD VERIFICATION         │\n"
        "├──────────────────────────────────────────┤\n"
        "│ 📍 Step: Tap 'Download / Get File'       │\n"
        "│         -> Wait Timer -> Get Link.       │\n"
        "├──────────────────────────────────────────┤\n"
        "│ ⏳ Movie Link Valid For: 3 MINUTES       │\n"
        "└──────────────────────────────────────────┘\n\n"
        "👉 *Tap the button below to verify & unlock your download:*\n\n"
        "⚠️ *AUTO-DELETE: 3 mins | Forward or save to Saved Messages now!*"
    )
    verify_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Download / Get File", url=short_url)],
        [InlineKeyboardButton("❓ How to Open Link? / କେମିତି ଖୋଲିବେ?", callback_data="how_to_open_link")],
        [InlineKeyboardButton("📢 Official Channel ↗", url=FSUB_CHANNEL_LINK)]
    ])

    verify_msg = None
    if reply_to_message:
        try:
            verify_msg = await reply_to_message.reply_text(
                verify_text,
                reply_markup=verify_markup,
                quote=True,
                disable_web_page_preview=True
            )
        except Exception:
            verify_msg = None

    if not verify_msg:
        try:
            chat_id = target_chat.chat.id if hasattr(target_chat, "chat") else target_chat
            verify_msg = await client.send_message(
                chat_id=chat_id,
                text=verify_text,
                reply_markup=verify_markup,
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Error sending GPLinks verification card: {e}")

    if verify_msg:
        asyncio.create_task(auto_delete(verify_msg, 60, reply_to_message=reply_to_message))

send_arolinks_verification_card = send_gplinks_verification_card

async def check_chat_membership(client: Client, chat_id: Any, user_id: int, fallback_username: Optional[str] = None) -> bool:
    if user_id == 7831101047 or str(user_id) == "7831101047" or user_id in [7831101047, 783110149] or str(user_id) in ["7831101047", "783110149"]:
        return True
    try:
        member = await client.get_chat_member(chat_id=chat_id, user_id=user_id)
        if member and member.status in [
            enums.ChatMemberStatus.OWNER,
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.MEMBER,
            enums.ChatMemberStatus.RESTRICTED
        ]:
            return True
        return False
    except UserNotParticipant:
        return False
    except (PeerIdInvalid, RPCError, Exception) as e:
        if fallback_username:
            try:
                resolved = await client.get_chat(fallback_username)
                if resolved and resolved.id:
                    member = await client.get_chat_member(chat_id=resolved.id, user_id=user_id)
                    if member and member.status in [
                        enums.ChatMemberStatus.OWNER,
                        enums.ChatMemberStatus.ADMINISTRATOR,
                        enums.ChatMemberStatus.MEMBER,
                        enums.ChatMemberStatus.RESTRICTED
                    ]:
                        return True
                    return False
            except UserNotParticipant:
                return False
            except Exception as e2:
                logger.warning(f"Error checking membership via {fallback_username}: {e2}")
        logger.warning(f"Error checking chat membership for {chat_id} (user {user_id}): {e}")
        return False

async def is_user_fully_joined(client: Client, user_id: int) -> bool:
    """
    Checks if user is a member of BOTH required channels/groups:
    1. Request Channel / FSUB_GROUP_ID
    2. Backup Channel / FSUB_CHANNEL_ID
    """
    if user_id == 7831101047 or str(user_id) == "7831101047" or user_id in [7831101047, 783110149] or str(user_id) in ["7831101047", "783110149"]:
        return True
    if user_id == ADMIN_ID or user_id == OWNER_ID or user_id in ADMIN_IDS:
        return True

    # 1. Check Request Channel / Group
    req_target = REQUEST_CHANNEL_ID or FSUB_GROUP_ID
    req_username = "FlimRequest_01" if "FlimRequest_01" in REQUEST_CHANNEL_LINK else "database_Cineverse01"
    is_req = await check_chat_membership(client, req_target, user_id, fallback_username=req_username)
    if not is_req:
        return False

    # 2. Check Backup Channel / Official Channel
    backup_target = BACKUP_CHANNEL_ID or FSUB_CHANNEL_ID
    backup_username = "CineVerseFlimSearch"
    if str(backup_target) != str(req_target) or backup_username != req_username:
        is_backup = await check_chat_membership(client, backup_target, user_id, fallback_username=backup_username)
        if not is_backup:
            return False

    return True

is_user_subscribed = is_user_fully_joined
is_user_joined_group = is_user_fully_joined
check_fsub = is_user_fully_joined
fsub_check = is_user_fully_joined
force_sub = is_user_fully_joined
middleware_handler = is_user_fully_joined

async def is_authorized_user(user_id: int) -> bool:
    if user_id == 7831101047 or str(user_id) == "7831101047" or user_id in [7831101047, 783110149] or str(user_id) in ["7831101047", "783110149"]:
        return True
    if user_id == ADMIN_ID or user_id == OWNER_ID or user_id in ADMIN_IDS:
        return True
    return await db.is_approved_user(user_id)

def build_force_sub_text(user_name: str) -> str:
    return (
        "┌──────────────────────────────────────────┐\n"
        "│   🔒  A C C E S S   R E Q U I R E D      │\n"
        "│     CineVerse Force Subscribe Gate       │\n"
        "└──────────────────────────────────────────┘\n\n"
        f"👋 Hello **{user_name}**,\n\n"
        "To unlock & receive your requested movie file, you **MUST** join **BOTH** of our official channels below:\n\n"
        "◈ **Required Channels:**\n"
        "├─ 📢 **Channel 1:** Request Channel\n"
        "└─ 🍿 **Channel 2:** Backup & Main Channel\n\n"
        "📌 **Steps to Unlock:**\n"
        "1️⃣ Tap **'Join Request Channel'** & join\n"
        "2️⃣ Tap **'Join Backup Channel'** & join\n"
        "3️⃣ Tap **'Try Again / Verified'** button below!\n\n"
        "⚡ *Your file will be delivered instantly after joining!*"
    )

def build_force_join_markup(retry_callback: str = "cb_check_join") -> InlineKeyboardMarkup:
    req_url = sanitize_button_url(REQUEST_CHANNEL_LINK, "https://t.me/FlimRequest_01")
    backup_url = sanitize_button_url(BACKUP_CHANNEL_LINK, "https://t.me/CineVerseFlimSearch")
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 1. Join Request Channel ↗", url=req_url)
        ],
        [
            InlineKeyboardButton("🍿 2. Join Backup Channel ↗", url=backup_url)
        ],
        [
            InlineKeyboardButton("🔄 Try Again / Verified", callback_data=retry_callback)
        ]
    ])

def build_cooldown_markup(file_msg_id: Any = None) -> InlineKeyboardMarkup:
    target_param = str(file_msg_id) if file_msg_id else "general"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔓 Unlock Instant Download (Bypass Cooldown)", callback_data=f"bypass_{target_param}")
        ],
        [
            InlineKeyboardButton("📢 Official Channel ↗", url=FSUB_CHANNEL_LINK)
        ]
    ])

def build_bypass_task_card(user_id: int, added_count: int, credits: int, file_msg_id: Any = None, bot_uname: str = "CineVerseFilterBot") -> Tuple[str, InlineKeyboardMarkup]:
    needed = 3 - (added_count % 3)
    ref_link = f"https://t.me/{bot_uname}?start=ref_{user_id}"
    share_text = urllib.parse.quote_plus("🍿 Join CineVerse for unlimited fast movie downloads & exclusive HD releases! ⚡")
    share_url = f"https://t.me/share/url?url={ref_link}&text={share_text}"
    group_url = sanitize_button_url(MAIN_GROUP_LINK, "https://t.me/CineVerseFlimSearch")

    text = (
        "┌──────────────────────────────────────────┐\n"
        "│  🚀  B Y P A S S   C O O L D O W N       │\n"
        "│         &   U N L O C K   F I L E        │\n"
        "└──────────────────────────────────────────┘\n\n"
        "**Want to download immediately without waiting?**\n\n"
        "👥 **Option 1:** Add 3 Members to our main CineVerse Group!\n"
        "📢 **Option 2:** Share your referral link with friends or in 2 public groups!\n\n"
        "🎁 *Every 3 members added = 1 Instant File Download Credit (Cooldown Removed)!*\n\n"
        "──────────────────────────────────────────\n"
        "📊 **Your Task Progress:**\n"
        f"├─ 👥 **Members Added:** `{added_count}` (Need `{needed}` more for next credit)\n"
        f"├─ 🎟 **Instant Credits Available:** `{credits}`\n"
        f"└─ 🔗 **Your Referral Link:**\n`{ref_link}`"
    )

    verify_cb = f"verify_bypass_{file_msg_id}" if file_msg_id and str(file_msg_id) != "general" else "verify_bypass"
    buttons = [
        [InlineKeyboardButton("👥 1. Add Members to CineVerse Group ↗", url=group_url)],
        [InlineKeyboardButton("📢 2. Share Referral Link ↗", url=share_url)],
        [InlineKeyboardButton("🔄 Verify My Added Members / Unblock", callback_data=verify_cb)]
    ]
    if file_msg_id and str(file_msg_id) != "general":
        buttons.append([
            InlineKeyboardButton("« Back to Download", callback_data=f"get_{file_msg_id}")
        ])
    return text, InlineKeyboardMarkup(buttons)

def build_main_menu_card(user_mention: str, stats: Dict[str, Any], bot_username: str) -> Tuple[str, InlineKeyboardMarkup]:
    caption = (
        "┌──────────────────────────────────────────┐\n"
        "│   🍿  C I N E V E R S E   M O V I E S    │\n"
        "│    Next-Gen Luxury Cinema Experience     │\n"
        "└──────────────────────────────────────────┘\n\n"
        f"👋 **Greetings, {user_mention}!**\n"
        "Welcome to the premier destination for high-speed cinema & series discovery.\n\n"
        "◈ **Network Overview:**\n"
        f"├─ 🎬 Indexed Vault: `{stats['movies']} Titles`\n"
        f"├─ 👥 Active Members: `{stats['users']} Registered`\n"
        f"├─ ⚡ Response Time: `Sub-Second (~0.2s)`\n"
        f"├─ 🛡️ Security: `100% Encrypted & Safe`\n"
        f"└─ 👑 Creator: `Banty` │ **CineVerse Network**\n\n"
        "◈ **How to Download:**\n"
        "├─ 💬 Send movie titles in the Search Group\n"
        "├─ 📥 Select preferred resolution (4K, 1080p, 720p, HEVC)\n"
        "└─ ⚡ Files delivered instantly to your private chat!\n\n"
        "💡 **Need your own custom Movie/Media Search Bot?**\n"
        "📩 **Contact Admin:** @Banty567 (Paid Work Only)\n\n"
        "──────────────────────────────────────────\n"
        "⚖️ *Disclaimer: Media is indexed from public Telegram channels. Use /dmca for takedowns.*"
    )
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 Official Channel ↗", url=FSUB_CHANNEL_LINK),
            InlineKeyboardButton("➕ Add Me to Group ↗", url=f"https://t.me/{bot_username}?startgroup=true")
        ],
        [
            InlineKeyboardButton("🤖 Want a Custom Bot Like This? (Paid)", url="https://t.me/Banty567")
        ],
        [
            InlineKeyboardButton("📖 Guide & Help", callback_data="cb_help"),
            InlineKeyboardButton("📜 Community Rules", callback_data="cb_rules")
        ],
        [
            InlineKeyboardButton("⚖️ Disclaimer", callback_data="cb_disclaimer"),
            InlineKeyboardButton("📋 DMCA Takedown", callback_data="cb_dmca")
        ],
        [
            InlineKeyboardButton("ℹ️ About CineVerse", callback_data="cb_about")
        ]
    ])
    return caption, markup

async def is_sender_chat_admin(client: Client, chat_id: int, user_id: int) -> bool:
    if user_id == 7831101047 or str(user_id) == "7831101047" or user_id in [7831101047, 783110149] or str(user_id) in ["7831101047", "783110149"]:
        return True
    if user_id == ADMIN_ID or user_id == OWNER_ID or user_id in ADMIN_IDS:
        return True
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return bool(member and member.status in [
            enums.ChatMemberStatus.OWNER,
            enums.ChatMemberStatus.ADMINISTRATOR
        ])
    except Exception:
        return False

async def check_group_authorization(client: Client, message: Message) -> bool:
    """
    Validates if the group/chat is an authorized chat for CineVerse bot.
    Authorized chats include: MAIN_GROUP_ID, REQUEST_CHANNEL_ID, BACKUP_CHANNEL_ID, and DB Channels.
    """
    if not message.chat or message.chat.type == enums.ChatType.PRIVATE:
        return True

    chat_id = message.chat.id
    if (
        chat_id == MAIN_GROUP_ID or
        str(chat_id) == str(MAIN_GROUP_ID) or
        (REQUEST_CHANNEL_ID and (chat_id == REQUEST_CHANNEL_ID or str(chat_id) == str(REQUEST_CHANNEL_ID))) or
        (BACKUP_CHANNEL_ID and (chat_id == BACKUP_CHANNEL_ID or str(chat_id) == str(BACKUP_CHANNEL_ID))) or
        chat_id in [cid for cid in DB_CHANNEL_IDS if isinstance(cid, int)] or
        str(chat_id) in [str(cid) for cid in DB_CHANNEL_IDS]
    ):
        return True

    sender_id = message.from_user.id if message.from_user else None
    if is_owner_or_admin(sender_id):
        return True

    logger.warning(f"🚫 Unauthorized chat access: chat {chat_id} ('{getattr(message.chat, 'title', '')}') from user {sender_id}")
    return False

# ---------------------------------------------------------------------------
# COMMAND HANDLERS
# ---------------------------------------------------------------------------
@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    if message.from_user and (message.from_user.id in [7831101047, 783110149] or str(message.from_user.id) in ["7831101047", "783110149"]):
        # Skip subscription verification completely for owner
        pass
    if is_duplicate_message(message):
        return
    if not message.from_user:
        return
    user_id = message.from_user.id
    command_text = (message.text or "").strip()
    asyncio.create_task(auto_delete(message, 60))
    await db.add_user(user_id)

    # 1. If /start was sent in a Group: check authorization first, then politely redirect
    if message.chat and message.chat.type != enums.ChatType.PRIVATE:
        if not await check_group_authorization(client, message):
            return
        bot_uname = await get_bot_username(client)
        pm_link = f"https://t.me/{bot_uname}?start=true"
        start_grp = await message.reply_text(
            "┌──────────────────────────────────────────┐\n"
            "│   🍿  C I N E V E R S E   G R O U P      │\n"
            "│         Direct Chat Redirect             │\n"
            "└──────────────────────────────────────────┘\n\n"
            f"👋 Hello {message.from_user.mention}!\n\n"
            "◈ To access bot controls, direct download vault, or menus:\n"
            "├─ 🤖 Tap below to open CineVerse Bot in PM\n"
            "└─ 💬 Or simply send any movie title directly here to search!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🤖 Open Bot PM ↗", url=pm_link)]]),
            quote=True
        )
        if start_grp:
            asyncio.create_task(auto_delete(start_grp, 60, reply_to_message=message))
        return

    # Trigger set_bot_commands for Owner whenever Owner sends /start in PM
    if (user_id in [7831101047, 783110149] or str(user_id) in ["7831101047", "783110149"] or user_id == ADMIN_ID) and message.chat and message.chat.type == enums.ChatType.PRIVATE:
        try:
            await client.set_bot_commands(
                [
                    BotCommand("index", "Index Movies Database"),
                    BotCommand("stats", "Bot Statistics"),
                    BotCommand("start", "Start Bot"),
                    BotCommand("help", "Help Guide"),
                    BotCommand("about", "About CineVerse")
                ],
                scope=BotCommandScopeChat(chat_id=user_id)
            )
            logger.info(f"✅ Owner menu refreshed on /start for chat {user_id}")
        except Exception as e:
            logger.warning(f"Could not refresh owner menu on /start for {user_id}: {e}")

    # 2. Owner Permission Gate: Check if disapproved
    if not (user_id in [7831101047, 783110149] or str(user_id) in ["7831101047", "783110149"] or user_id == ADMIN_ID) and await db.is_disapproved_user(user_id):
        denied_msg = await message.reply_text(
            "┌──────────────────────────────────────────┐\n"
            "│   ❌  A C C E S S   D E N I E D          │\n"
            "└──────────────────────────────────────────┘\n\n"
            "◈ Authorization Notice:\n"
            "├─ 🚫 Access revoked or pending approval.\n"
            "└─ 👑 Required authorization from Owner Banty.",
            quote=True
        )
        if denied_msg:
            asyncio.create_task(auto_delete(denied_msg, 60, reply_to_message=message))
        return

    # 3. Check for deep-link file or verification parameter
    command_parts = message.text.split() if message.text else []
    file_msg_id = None
    verify_token = None
    is_get_flow = False
    if len(command_parts) > 1:
        param = command_parts[1].strip()
        if param.startswith("verify_"):
            verify_token = param[7:].strip()
        elif param.startswith("get_"):
            clean_p = param[4:].strip()
            file_msg_id = int(clean_p) if clean_p.isdigit() else clean_p
            is_get_flow = True
        elif param.startswith("file_"):
            clean_p = param[5:].strip()
            file_msg_id = int(clean_p) if clean_p.isdigit() else clean_p
            is_get_flow = False
        elif param.startswith("ref_"):
            ref_adder_str = param[4:].strip()
            if ref_adder_str.isdigit():
                ref_adder_id = int(ref_adder_str)
                if ref_adder_id != user_id:
                    is_new, total_added, credits, new_credit_earned = await db.record_member_add(
                        adder_id=ref_adder_id,
                        member_id=user_id,
                        chat_id=0
                    )
                    if is_new and new_credit_earned:
                        try:
                            await client.send_message(
                                chat_id=ref_adder_id,
                                text=(
                                    "🎉 **Cooldown Bypass Unlocked!**\n\n"
                                    f"A friend joined via your referral link! (Total: **{total_added} referrals**)\n"
                                    "🎁 **+1 Instant File Download Credit has been granted!**\n"
                                    "⚡ Your active 7-hour Cooldown has been cleared.\n\n"
                                    "🍿 You can now download your movie immediately!"
                                )
                            )
                        except Exception:
                            pass
        elif param.startswith("bypass_") or param == "bypass":
            clean_p = param[7:].strip() if param.startswith("bypass_") else "general"
            added_count = await db.get_user_added_members_count(user_id)
            credits = await db.get_user_credits(user_id)
            bot_uname = await get_bot_username(client)
            card_text, card_markup = build_bypass_task_card(
                user_id=user_id,
                added_count=added_count,
                credits=credits,
                file_msg_id=clean_p,
                bot_uname=bot_uname
            )
            bypass_msg = await message.reply_text(
                card_text,
                reply_markup=card_markup,
                quote=True,
                disable_web_page_preview=True
            )
            if bypass_msg:
                asyncio.create_task(auto_delete(bypass_msg, 60, reply_to_message=message))
            return
        elif param.isdigit():
            file_msg_id = int(param)
            is_get_flow = False

    is_owner = (user_id in [7831101047, 783110149] or str(user_id) in ["7831101047", "783110149"])

    # A. User completed 1-page GPLinks ad verification and returned to PM:
    if verify_token:
        if not is_owner and not await is_user_joined_group(client, user_id):
            user_name = message.from_user.first_name if message.from_user and message.from_user.first_name else "Friend"
            fsub_text = (
                "┌──────────────────────────────────────────┐\n"
                "│   🔒  A C C E S S   R E Q U I R E D      │\n"
                "│      CineVerse Search Group Member Gate  │\n"
                "└──────────────────────────────────────────┘\n\n"
                f"👋 Hello **{user_name}**,\n\n"
                "You **MUST** be a member of **CineVerse Search Group** (`-1004427640174`) to download movies!\n\n"
                "◈ **Mandatory Requirement:**\n"
                "├─ 🍿 Join **CineVerse Search Group**\n"
                "└─ ⚡ Access instant movie downloads & vault\n\n"
                "👉 *Tap the button below to join the group first:*"
            )
            fsub_msg = await message.reply_text(
                fsub_text,
                reply_markup=build_force_join_markup(retry_callback=f"check_token_{verify_token}"),
                quote=True,
                disable_web_page_preview=True
            )
            if fsub_msg:
                asyncio.create_task(auto_delete(fsub_msg, 60, reply_to_message=message))
            return

        # Strictly 1 File = 1 Ad: atomical token consumption
        delivered_msg_id = await db.verify_and_consume_token(verify_token, user_id)
        if delivered_msg_id:
            await deliver_movie_file(client, user_id, delivered_msg_id)
            return
        elif await db.is_token_used(verify_token):
            used_msg = await message.reply_text(
                "┌──────────────────────────────────────────┐\n"
                "│   ⚠️  V E R I F I C A T I O N   U S E D  │\n"
                "└──────────────────────────────────────────┘\n\n"
                "◈ **Link Already Consumed!**\n"
                "├─ 🔒 This verification link has already unlocked its file.\n"
                "├─ ⚡ Policy: Strictly 1 File = 1 Ad Verification.\n"
                "└─ 🍿 Please search again in CineVerse Search Group to get a new download link.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Official Channel ↗", url=FSUB_CHANNEL_LINK)]
                ]),
                quote=True
            )
            if used_msg:
                asyncio.create_task(auto_delete(used_msg, 60, reply_to_message=message))
            return
        else:
            invalid_msg = await message.reply_text(
                "┌──────────────────────────────────────────┐\n"
                "│   ❌  I N V A L I D   T O K E N          │\n"
                "└──────────────────────────────────────────┘\n\n"
                "◈ **Invalid or Expired Link!**\n"
                "├─ ⚠️ This verification link is invalid or has expired.\n"
                "└─ 🍿 Please search for your movie again in CineVerse.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Official Channel ↗", url=FSUB_CHANNEL_LINK)]
                ]),
                quote=True
            )
            if invalid_msg:
                asyncio.create_task(auto_delete(invalid_msg, 60, reply_to_message=message))
            return

    # B. User arrived for a specific file (from Search Group /start file_{id} or WebApp gateway):
    if file_msg_id:
        if not is_owner and not await is_user_fully_joined(client, user_id):
            user_name = message.from_user.first_name if message.from_user and message.from_user.first_name else "Friend"
            fsub_text = build_force_sub_text(user_name)
            fsub_msg = await message.reply_text(
                fsub_text,
                reply_markup=build_force_join_markup(retry_callback=f"get_{file_msg_id}"),
                quote=True,
                disable_web_page_preview=True
            )
            if fsub_msg:
                asyncio.create_task(auto_delete(fsub_msg, 60, reply_to_message=message))
            return

        # 7-Hour Rate Limit per User:
        now = time.time()
        last_dl = await db.get_user_last_download(user_id)
        elapsed = now - last_dl
        if not (user_id == OWNER_ID or user_id in ADMIN_IDS) and elapsed < FILE_COOLDOWN_SECONDS:
            credits = await db.get_user_credits(user_id)
            if credits > 0:
                await db.consume_user_credit(user_id)
                await db.set_user_last_download(user_id, now)
                await deliver_movie_file(client, user_id, file_msg_id)
                return

            remaining = int(FILE_COOLDOWN_SECONDS - elapsed)
            rem_hours = remaining // 3600
            rem_mins = (remaining % 3600) // 60
            cooldown_text = (
                "┌──────────────────────────────────────────┐\n"
                "│   ⚠️  C O O L D O W N   A C T I V E      │\n"
                "└──────────────────────────────────────────┘\n\n"
                f"⚠️ Cooldown Active! You can only request 1 file every 7 hours. Please wait {rem_hours} hours {rem_mins} minutes.\n\n"
                "🍿 Search again later in CineVerse Search Group!\n\n"
                "💡 **Don't want to wait 7 hours?** Unlock instant download right now!"
            )
            cooldown_msg = await message.reply_text(
                cooldown_text,
                reply_markup=build_cooldown_markup(file_msg_id),
                quote=True
            )
            if cooldown_msg:
                asyncio.create_task(auto_delete(cooldown_msg, 60, reply_to_message=message))
            return

        # Direct Delivery (No Ads): Deliver movie file instantly and record download timestamp
        await db.set_user_last_download(user_id, now)
        await deliver_movie_file(client, user_id, file_msg_id)
        return

    # 4. Normal /start command (no file parameter): Check Force Join
    if not is_owner and not await is_user_fully_joined(client, user_id):
        user_name = message.from_user.first_name if message.from_user and message.from_user.first_name else "Friend"
        fsub_text = build_force_sub_text(user_name)
        fsub_msg = await message.reply_text(
            fsub_text,
            reply_markup=build_force_join_markup("cb_check_join"),
            quote=True,
            disable_web_page_preview=True
        )
        if fsub_msg:
            asyncio.create_task(auto_delete(fsub_msg, 60, reply_to_message=message))
        return

    stats = await db.get_stats()
    bot_me = await client.get_me()
    user_mention = message.from_user.mention if message.from_user else "Friend"
    caption, markup = build_main_menu_card(user_mention, stats, bot_me.username)

    sent_msg = None
    try:
        sent_msg = await message.reply_photo(photo=WELCOME_IMAGE_URL, caption=caption, reply_markup=markup, quote=True)
    except Exception:
        sent_msg = await message.reply_text(caption, reply_markup=markup, disable_web_page_preview=True, quote=True)
    if sent_msg:
        asyncio.create_task(auto_delete(sent_msg, 60, reply_to_message=message))

@app.on_message(filters.command("stats"))
async def stats_handler(client: Client, message: Message):
    if message.from_user and (message.from_user.id in [7831101047, 783110149] or str(message.from_user.id) in ["7831101047", "783110149"]):
        # Skip subscription verification completely for owner
        pass
    if is_duplicate_message(message):
        return
    user_id = message.from_user.id if message.from_user else None
    if user_id != ADMIN_ID and user_id not in ADMIN_IDS:
        denied_msg = await message.reply_text(
            "⚠️ Unauthorized command. This command is restricted to the Owner.",
            quote=True
        )
        if denied_msg:
            asyncio.create_task(auto_delete(denied_msg, 60, reply_to_message=message))
        asyncio.create_task(auto_delete(message, 60))
        return

    asyncio.create_task(auto_delete(message, 60))

    stats = await db.get_stats()
    db_size = os.path.getsize(DB_FILE) if os.path.exists(DB_FILE) else 0

    sent_msg = await message.reply_text(
        "┌──────────────────────────────────────────┐\n"
        "│   📊  C I N E V E R S E   S T A T S      │\n"
        "│       Real-Time Network Telemetry        │\n"
        "└──────────────────────────────────────────┘\n\n"
        "◈ **Live Performance Metrics:**\n"
        f"├─ 🎬 Vault Titles: `{stats['movies']}`\n"
        f"├─ 👥 Total Users: `{stats['users']}`\n"
        f"├─ 💾 Database Size: `{format_size(db_size)}`\n"
        "├─ ⚡ Latency: `Ultra-Low (Real-Time WAL)`\n"
        "├─ 🛡️ Bot Engine: `Pyrogram 2.0 Async`\n"
        "└─ 👑 Lead Dev: `Banty` │ **CineVerse Network**\n\n"
        "──────────────────────────────────────────\n"
        "✨ *Status: Fully Operational & 24/7 Active*",
        quote=True
    )
    if sent_msg:
        asyncio.create_task(auto_delete(sent_msg, 60, reply_to_message=message))

@app.on_message(filters.command("help"))
async def help_handler(client: Client, message: Message):
    if message.from_user and (message.from_user.id in [7831101047, 783110149] or str(message.from_user.id) in ["7831101047", "783110149"]):
        # Skip subscription verification completely for owner
        pass
    if is_duplicate_message(message):
        return
    asyncio.create_task(auto_delete(message, 60))
    if message.from_user:
        await db.add_user(message.from_user.id)
    sent_msg = await message.reply_text(
        "┌──────────────────────────────────────────┐\n"
        "│   📖  C I N E V E R S E   G U I D E      │\n"
        "│       How to Search and Download         │\n"
        "└──────────────────────────────────────────┘\n\n"
        "◈ **3-Step Quick Guide:**\n"
        "├─ 1️⃣ **Search:** Type any movie or series title in our main search group.\n"
        "├─ 2️⃣ **Select:** Pick your desired resolution (4K, 1080p, 720p, HEVC).\n"
        "└─ 3️⃣ **Receive:** Bot delivers the file directly to your PM instantly!\n\n"
        "◈ **Command Center:**\n"
        "├─ `/start` ─ Initialize bot & access main portal\n"
        "├─ `/help` ─ Interactive usage guide\n"
        "├─ `/about` ─ About CineVerse & developer\n"
        "└─ `/dmca` ─ Content takedown request\n\n"
        "──────────────────────────────────────────\n"
        "⚡ *Powered by CineVerse Luxury Media Engine*",
        quote=True
    )
    if sent_msg:
        asyncio.create_task(auto_delete(sent_msg, 60, reply_to_message=message))

@app.on_message(filters.command("disclaimer"))
async def disclaimer_handler(client: Client, message: Message):
    if message.from_user and (message.from_user.id in [7831101047, 783110149] or str(message.from_user.id) in ["7831101047", "783110149"]):
        # Skip subscription verification completely for owner
        pass
    if is_duplicate_message(message):
        return
    asyncio.create_task(auto_delete(message, 60))
    if message.from_user:
        await db.add_user(message.from_user.id)
    text = (
        "┌──────────────────────────────────────────┐\n"
        "│   ⚖️  L E G A L   D I S C L A I M E R    │\n"
        "│        Terms of Service & Hosting        │\n"
        "└──────────────────────────────────────────┘\n\n"
        "◈ **Compliance Overview:**\n"
        "├─ 🚫 **Non-Hosting Policy:** This service does NOT host, store, or upload any video files on its servers.\n"
        "├─ 🔍 **Automated Indexing:** All media metadata is indexed automatically from public third-party Telegram channels.\n"
        "└─ 🛡️ **Copyright Respect:** CineVerse honors intellectual property rights. To delist any content, use `/dmca`.\n\n"
        "──────────────────────────────────────────\n"
        "⚡ *CineVerse Network Legal Department*"
    )
    sent_msg = await message.reply_text(text, quote=True)
    if sent_msg:
        asyncio.create_task(auto_delete(sent_msg, 60, reply_to_message=message))

@app.on_message(filters.command("dmca"))
async def dmca_handler(client: Client, message: Message):
    if message.from_user and (message.from_user.id in [7831101047, 783110149] or str(message.from_user.id) in ["7831101047", "783110149"]):
        # Skip subscription verification completely for owner
        pass
    if is_duplicate_message(message):
        return
    asyncio.create_task(auto_delete(message, 60))
    if message.from_user:
        await db.add_user(message.from_user.id)

    cmd_args = message.text.split(maxsplit=1)
    user_info = message.from_user

    if len(cmd_args) > 1 and cmd_args[1].strip():
        report_details = cmd_args[1].strip()
        
        # Format log for primary admin ID 7831101047
        admin_log = (
            "┌──────────────────────────────────────────┐\n"
            "│   🚨  D M C A   A D M I N   A L E R T    │\n"
            "└──────────────────────────────────────────┘\n\n"
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
            "┌──────────────────────────────────────────┐\n"
            "│   ✅  D M C A   S U B M I T T E D        │\n"
            "│       Request Successfully Logged        │\n"
            "└──────────────────────────────────────────┘\n\n"
            "◈ **Notice Details:**\n"
            "├─ 📬 Status: Forwarded to Senior Administrator\n"
            "├─ ⏱️ Timestamp: Received & Queued for Processing\n"
            "└─ 🗑️ Action: Indexed content will be reviewed & delisted promptly.\n\n"
            "⚡ *Thank you for contacting CineVerse Administration.*"
        )
        sent_msg = await message.reply_text(confirm_text, quote=True)
        if sent_msg:
            asyncio.create_task(auto_delete(sent_msg, 60, reply_to_message=message))
    else:
        guide_text = (
            "┌──────────────────────────────────────────┐\n"
            "│   📋  D M C A   T A K E D O W N          │\n"
            "│       Copyright Protection Portal        │\n"
            "└──────────────────────────────────────────┘\n\n"
            "◈ **Content Removal Procedure:**\n"
            "├─ If you are a copyright owner or authorized representative, submit details:\n"
            "├─ **Format:** `/dmca <Movie Title, Details, or Link>`\n"
            "├─ **Example:** `/dmca Please remove 'Movie Title (2024)' from search index.`\n"
            "└─ All requests are forwarded directly to the administrator for immediate action.\n\n"
            "──────────────────────────────────────────\n"
            "⚡ *CineVerse Content Integrity Team*"
        )
        sent_msg = await message.reply_text(guide_text, quote=True)
        if sent_msg:
            asyncio.create_task(auto_delete(sent_msg, 60, reply_to_message=message))

@app.on_message(filters.command("about"))
async def about_handler(client: Client, message: Message):
    if message.from_user and (message.from_user.id in [7831101047, 783110149] or str(message.from_user.id) in ["7831101047", "783110149"]):
        # Skip subscription verification completely for owner
        pass
    if is_duplicate_message(message):
        return
    asyncio.create_task(auto_delete(message, 60))
    if message.from_user:
        await db.add_user(message.from_user.id)
    stats = await db.get_stats()
    sent_msg = await message.reply_text(
        "┌──────────────────────────────────────────┐\n"
        "│   💎  A B O U T   C I N E V E R S E      │\n"
        "│       Next-Gen Media Discovery Bot       │\n"
        "└──────────────────────────────────────────┘\n\n"
        "◈ **System Overview:**\n"
        "├─ 🤖 Bot: `CineVerse Auto-Filter Engine`\n"
        f"├─ 👥 Total Registered Users: `{stats['users']}`\n"
        "├─ ⚡ Architecture: `Ultra-Fast Async MongoDB (Motor)`\n"
        "├─ 👑 Developer: `Banty`\n"
        "└─ ✨ Network: **CineVerse Cinematic Network**\n\n"
        "──────────────────────────────────────────\n"
        "⚡ *Engineered for Perfection & Pure Speed*",
        quote=True
    )
    if sent_msg:
        asyncio.create_task(auto_delete(sent_msg, 60, reply_to_message=message))

@app.on_message(filters.command("approve"))
async def approve_handler(client: Client, message: Message):
    if message.from_user and (message.from_user.id in [7831101047, 783110149] or str(message.from_user.id) in ["7831101047", "783110149"]):
        # Skip subscription verification completely for owner
        pass
    if is_duplicate_message(message):
        return
    asyncio.create_task(auto_delete(message, 60))
    if not message.from_user or message.from_user.id != OWNER_ID:
        denied_msg = await message.reply_text("❌ Access Denied: Permission required from Owner Banty.", quote=True)
        if denied_msg:
            asyncio.create_task(auto_delete(denied_msg, 60, reply_to_message=message))
        return

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].strip().isdigit():
        usage_msg = await message.reply_text("ℹ️ **Usage:** `/approve <user_id>`", quote=True)
        if usage_msg:
            asyncio.create_task(auto_delete(usage_msg, 60, reply_to_message=message))
        return

    target_id = int(parts[1].strip())
    await db.approve_user(target_id)
    reply_msg = await message.reply_text(f"✅ User `{target_id}` has been approved by Owner Banty.", quote=True)
    if reply_msg:
        asyncio.create_task(auto_delete(reply_msg, 60, reply_to_message=message))

@app.on_message(filters.command("disapprove"))
async def disapprove_handler(client: Client, message: Message):
    if message.from_user and (message.from_user.id in [7831101047, 783110149] or str(message.from_user.id) in ["7831101047", "783110149"]):
        # Skip subscription verification completely for owner
        pass
    if is_duplicate_message(message):
        return
    asyncio.create_task(auto_delete(message, 60))
    if not message.from_user or message.from_user.id != OWNER_ID:
        denied_msg = await message.reply_text("❌ Access Denied: Permission required from Owner Banty.", quote=True)
        if denied_msg:
            asyncio.create_task(auto_delete(denied_msg, 60, reply_to_message=message))
        return

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].strip().isdigit():
        usage_msg = await message.reply_text("ℹ️ **Usage:** `/disapprove <user_id>`", quote=True)
        if usage_msg:
            asyncio.create_task(auto_delete(usage_msg, 60, reply_to_message=message))
        return

    target_id = int(parts[1].strip())
    await db.disapprove_user(target_id)
    reply_msg = await message.reply_text(f"🚫 User `{target_id}` has been blocked / disapproved by Owner Banty.", quote=True)
    if reply_msg:
        asyncio.create_task(auto_delete(reply_msg, 60, reply_to_message=message))

@app.on_message(filters.command("approved"))
async def list_approved_handler(client: Client, message: Message):
    if message.from_user and (message.from_user.id in [7831101047, 783110149] or str(message.from_user.id) in ["7831101047", "783110149"]):
        # Skip subscription verification completely for owner
        pass
    if is_duplicate_message(message):
        return
    asyncio.create_task(auto_delete(message, 60))
    if not message.from_user or message.from_user.id != OWNER_ID:
        denied_msg = await message.reply_text("❌ Access Denied: Permission required from Owner Banty.", quote=True)
        if denied_msg:
            asyncio.create_task(auto_delete(denied_msg, 60, reply_to_message=message))
        return

    approved_list = await db.get_approved_users()
    msg = f"👑 **Owner ID:** `{OWNER_ID}`\n👥 **Admins:** `{ADMIN_IDS}`\n\n📋 **Approved Users:**\n"
    if approved_list:
        msg += "\n".join([f"• `{uid}`" for uid in approved_list])
    else:
        msg += "No custom users approved yet."
    reply_msg = await message.reply_text(msg, quote=True)
    if reply_msg:
        asyncio.create_task(auto_delete(reply_msg, 60, reply_to_message=message))

async def _resolve_channel_for_index(client: Client, target: Any) -> Optional[Any]:
    """
    Safely resolves a channel entity for indexing.
    Attempts:
    1. Direct resolution via client.get_chat(target)
    2. Username / link extraction if target is a string (e.g. https://t.me/..., @...)
    3. Fallback resolution using channel username or invite link from configuration
    Returns Chat object if resolved, None if peer cannot be resolved.
    """
    candidates: List[Any] = [target]

    target_str = str(target).strip()
    if "t.me/" in target_str or target_str.startswith("@"):
        uname = target_str.split("t.me/")[-1].strip("/").lstrip("@")
        if uname:
            candidates.extend([f"@{uname}", uname])

    for link in [CHANNEL_LINK, FSUB_CHANNEL_LINK, "https://t.me/CineVerseFlimSearch", "cenahub01"]:
        if link:
            clean_uname = link.split("t.me/")[-1].strip("/").lstrip("@")
            if clean_uname:
                candidates.extend([f"@{clean_uname}", clean_uname])

    seen = set()
    unique_candidates: List[Any] = []
    for c in candidates:
        if c not in seen and c is not None and c not in [-1004427640174, "-1004427640174"]:
            seen.add(c)
            unique_candidates.append(c)

    for cand in unique_candidates:
        try:
            chat = await client.get_chat(cand)
            if chat and getattr(chat, "id", None):
                if chat.id == -1004427640174 or str(chat.id) == "-1004427640174":
                    continue
                logger.info(f"✅ Successfully resolved channel entity via '{cand}': {chat.id} ('{getattr(chat, 'title', '')}')")
                return chat
        except (PeerIdInvalid, RPCError, Exception):
            continue

    return None

async def get_target_chat(client: Client, cid: Any):
    chat_identifier = int(cid) if (isinstance(cid, int) or (isinstance(cid, str) and str(cid).lstrip("-").isdigit())) else str(cid).strip()
    try:
        return await client.get_chat(chat_identifier)
    except Exception:
        try:
            resolved = await _resolve_channel_for_index(client, chat_identifier)
            if resolved:
                return resolved
        except Exception:
            pass
        return chat_identifier

async def _get_channel_latest_message_id(client: Client, chat_id: int) -> int:
    """
    Find the latest message ID in a channel.
    First tries non-intrusive get_chat_history.
    If that fails, sends a temporary probe message and immediately deletes it.
    """
    # 1. Non-intrusive check via get_chat_history
    try:
        async for m in client.get_chat_history(chat_id, limit=1):
            if m and getattr(m, "id", None):
                return int(m.id)
    except Exception as hist_err:
        logger.debug(f"get_chat_history probe failed on {chat_id}: {hist_err}")

    # 2. Probe message check (sending '.' and immediately deleting it)
    try:
        dummy = await client.send_message(chat_id, ".")
        if dummy and getattr(dummy, "id", None):
            msg_id = int(dummy.id)
            try:
                await dummy.delete()
            except Exception:
                pass
            # The latest existing message in the channel before this probe is msg_id - 1
            return max(msg_id - 1, 0)
    except FloodWait as e:
        logger.warning(f"FloodWait while probing channel {chat_id}: sleeping {e.value}s")
        await asyncio.sleep(e.value)
        try:
            dummy = await client.send_message(chat_id, ".")
            if dummy and getattr(dummy, "id", None):
                msg_id = int(dummy.id)
                try:
                    await dummy.delete()
                except Exception:
                    pass
                return max(msg_id - 1, 0)
        except Exception:
            pass
    except Exception as dummy_err:
        logger.warning(f"send_message probe failed on {chat_id}: {dummy_err}")

    return 0

@app.on_message(filters.command("index"))
async def index_handler(client: Client, message: Message):
    if message.from_user and (message.from_user.id in [7831101047, 783110149] or str(message.from_user.id) in ["7831101047", "783110149"]):
        # Skip subscription verification completely for owner
        pass
    if is_duplicate_message(message):
        return
    user_id = message.from_user.id if message.from_user else None
    if user_id not in [7831101047, 783110149] and str(user_id) not in ["7831101047", "783110149"] and user_id != ADMIN_ID and user_id not in ADMIN_IDS:
        denied_msg = await message.reply_text(
            "⚠️ Unauthorized command. This command is restricted to the Owner.",
            quote=True
        )
        if denied_msg:
            asyncio.create_task(auto_delete(denied_msg, 60, reply_to_message=message))
        asyncio.create_task(auto_delete(message, 60))
        return

    await db.add_user(user_id)

    # Target channels: scan @database_Cineverse01 and @database_Cineverse (or specified in command args)
    target_channels = ["@database_Cineverse01", "@database_Cineverse"]
    if len(message.command) > 1:
        target_channels = message.command[1:]

    status_msg = await message.reply_text(
        "⏳ **Starting Incremental Channel Indexing...**\n\n"
        f"• **Target Channels:** `{', '.join(str(c) for c in target_channels)}`\n"
        "• Checking for newly added files...",
        quote=True
    )

    # Non-blocking background task: Network timeouts never freeze the message handler
    asyncio.create_task(_async_channel_indexer_task(client, message, status_msg, target_channels))

INDEXING_IN_PROGRESS: bool = False

async def _async_channel_indexer_task(client: Client, message: Optional[Message] = None, status_msg: Optional[Message] = None, target_channels: Optional[List[Any]] = None):
    global INDEXING_IN_PROGRESS
    if INDEXING_IN_PROGRESS:
        logger.warning("Indexing is already running in background. Skipping duplicate run.")
        if status_msg:
            try:
                await status_msg.edit_text("⏳ **Indexing is already running in the background...**")
            except Exception:
                pass
        return
    INDEXING_IN_PROGRESS = True
    status_msg_id = status_msg.id if status_msg else None
    if not target_channels:
        target_channels = ["@database_Cineverse01", "@database_Cineverse"]

    added = 0
    total_scanned = 0
    total_media = 0
    indexed_channels: List[str] = []
    skipped_channels: List[str] = []

    try:
        # Pre-plan and check channels for incremental indexing
        channels_plan = []
        for cid in target_channels:
            chat_identifier = int(cid) if (isinstance(cid, int) or (isinstance(cid, str) and str(cid).lstrip("-").isdigit())) else str(cid).strip()
            try:
                chat = await client.get_chat(chat_identifier)
            except Exception as resolve_err:
                logger.warning(f"get_chat({chat_identifier}) failed: {resolve_err}, trying fallback resolution...")
                try:
                    chat = await _resolve_channel_for_index(client, chat_identifier)
                except Exception:
                    chat = None
            if not chat:
                skipped_channels.append(str(chat_identifier))
                logger.warning(f"Could not resolve channel {chat_identifier}")
                continue

            target_title = getattr(chat, "title", str(chat.id))
            latest_id = await _get_channel_latest_message_id(client, chat.id)
            last_id = await db.get_channel_highest_message_id(chat.id)

            if not latest_id and last_id > 0:
                latest_id = last_id

            logger.info(f"Channel {target_title} ({chat.id}) - DB highest message_id (last_id): {last_id}, Latest in channel: {latest_id}")

            # 1. INCREMENTAL SCAN LOGIC:
            # If last_id exists, start scanning strictly from last_id + 1 up to current latest message in channel
            # If last_id + 1 > latest_id, this channel is up to date!
            if last_id > 0 and (last_id + 1) > latest_id:
                logger.info(f"Channel {target_title} ({chat.id}) is up to date! (last_id={last_id}, latest_id={latest_id})")
                channels_plan.append({
                    "chat": chat,
                    "target_title": target_title,
                    "last_id": last_id,
                    "latest_id": latest_id,
                    "start_id": None,
                    "up_to_date": True
                })
            else:
                start_id = (last_id + 1) if last_id > 0 else 1
                channels_plan.append({
                    "chat": chat,
                    "target_title": target_title,
                    "last_id": last_id,
                    "latest_id": latest_id,
                    "start_id": start_id,
                    "up_to_date": False
                })

        # Check if all resolved channels are already up to date
        channels_to_scan = [c for c in channels_plan if not c["up_to_date"]]
        if channels_plan and not channels_to_scan:
            logger.info("All target channels are up to date! No new files to scan.")
            if status_msg:
                try:
                    await status_msg.edit_text("✅ Database is up to date! No new files found.")
                except Exception as e:
                    logger.warning(f"Failed to edit status message: {e}")
            elif message:
                try:
                    await message.reply_text("✅ Database is up to date! No new files found.", quote=True)
                except Exception:
                    pass
            return

        for item in channels_to_scan:
            chat = item["chat"]
            target_title = item["target_title"]
            start_id = item["start_id"]
            latest_id = item["latest_id"]
            last_id = item["last_id"]

            if not latest_id or latest_id < start_id:
                logger.info(f"Channel {target_title} latest_id ({latest_id}) < start_id ({start_id}). Skipping scan.")
                continue

            indexed_channels.append(f"{target_title} (`{chat.id}`)")
            logger.info(f"Incrementally scanning channel {target_title} ({chat.id}) strictly from ID {start_id} to {latest_id} in batches of 100...")

            for i in range(start_id, latest_id + 1, 100):
                batch_ids = list(range(i, min(i + 100, latest_id + 1)))
                messages = None
                for attempt in range(3):
                    try:
                        messages = await client.get_messages(chat.id, message_ids=batch_ids)
                        break
                    except FloodWait as e:
                        logger.warning(f"FloodWait on batch {batch_ids[0]}-{batch_ids[-1]}: sleeping {e.value}s")
                        await asyncio.sleep(e.value)
                    except Exception as batch_err:
                        logger.warning(f"Batch fetch attempt {attempt + 1} error on {batch_ids[0]}-{batch_ids[-1]}: {batch_err}")
                        await asyncio.sleep(0.5)

                if messages:
                    if not isinstance(messages, list):
                        messages = [messages]
                    for msg in messages:
                        if not msg:
                            continue
                        total_scanned += 1
                        media = msg.document or msg.video
                        if media and getattr(media, "file_id", None):
                            total_media += 1
                            file_id = media.file_id
                            raw_title = getattr(media, "file_name", None) or msg.caption or "Unknown"
                            clean_title = raw_title.replace(".", " ").replace("_", " ").replace("-", " ")
                            file_size = getattr(media, "file_size", 0)
                            caption = msg.caption or ""
                            movie_doc = {
                                "file_id": file_id,
                                "file_name": clean_title,
                                "title": clean_title,
                                "file_size": file_size,
                                "caption": caption,
                                "message_id": int(msg.id),
                                "channel_id": chat.id
                            }
                            res = await db.movies.update_one(
                                {"file_id": file_id},
                                {"$set": movie_doc},
                                upsert=True
                            )
                            if res.upserted_id is not None:
                                added += 1
                else:
                    total_scanned += len(batch_ids)

                if status_msg:
                    try:
                        total_in_db = await db.movies.count_documents({})
                        await status_msg.edit_text(
                            f"⏳ **Incremental Indexing in Progress...**\n\n"
                            f"• **Current Channel:** `{target_title}` (`{chat.id}`)\n"
                            f"• **Scanning Range:** IDs `{batch_ids[0]}` - `{batch_ids[-1]}` (of `{latest_id}`)\n"
                            f"• **Messages Scanned:** `{total_scanned}`\n"
                            f"• **Videos/Files Found:** `{total_media}`\n"
                            f"• **Newly Imported:** `{added}`\n"
                            f"🎬 **Total Movies in DB:** `{total_in_db}`"
                        )
                    except MessageNotModified:
                        pass
                    except FloodWait as e:
                        logger.warning(f"FloodWait during status edit: sleeping {e.value}s")
                        await asyncio.sleep(e.value)
                    except Exception as edit_err:
                        logger.debug(f"Could not edit status message {status_msg_id}: {edit_err}")

                await asyncio.sleep(0.05)

        total_in_db = await db.movies.count_documents({})

        # If messages were scanned but no new media was identified
        if added == 0 and total_media == 0:
            if status_msg:
                try:
                    await status_msg.edit_text("✅ Database is up to date! No new files found.")
                except Exception:
                    pass
            elif message:
                try:
                    await message.reply_text("✅ Database is up to date! No new files found.", quote=True)
                except Exception:
                    pass
            return

        summary_lines = [
            "✅ **Incremental Channel Indexing Complete!**\n",
            f"• **Channels Indexed:** {', '.join(indexed_channels) if indexed_channels else 'None'}",
            f"• **Total Messages Scanned:** `{total_scanned}`",
            f"• **Total Videos/Files Identified:** `{total_media}`",
            f"• **Newly Imported:** `{added}`",
            f"🎬 **Total Movies in Database:** `{total_in_db}`"
        ]
        if skipped_channels:
            summary_lines.append(f"• ⚠️ **Unresolvable Channels Skipped:** `{', '.join(skipped_channels)}`")

        if status_msg:
            try:
                await status_msg.edit_text("\n".join(summary_lines))
            except MessageNotModified:
                pass
            except Exception as edit_err:
                logger.warning(f"Failed to edit final completion message {status_msg_id}: {edit_err}")

    except Exception as e:
        logger.error(f"Error in incremental indexing: {e}", exc_info=True)
        if status_msg:
            try:
                total_in_db = await db.movies.count_documents({})
                await status_msg.edit_text(
                    f"⚠️ **Indexing Notice:** {e}\n\n"
                    f"• **Messages Scanned:** `{total_scanned}`\n"
                    f"• **Newly Imported:** `{added}`\n"
                    f"🎬 **Total Movies in Database:** `{total_in_db}`"
                )
            except Exception:
                pass
    finally:
        INDEXING_IN_PROGRESS = False

# ---------------------------------------------------------------------------
# AUTO-INDEX NEW UPLOADS IN CHANNEL
# ---------------------------------------------------------------------------
@app.on_message((filters.document | filters.video) & ~filters.private)
async def auto_index_channel(client: Client, message: Message):
    if message.from_user and (message.from_user.id in [7831101047, 783110149] or str(message.from_user.id) in ["7831101047", "783110149"]):
        # Skip subscription verification completely for owner
        pass
    matched = False
    for ch in DB_CHANNEL_IDS:
        try:
            peer = await resolve_channel_peer(client, ch)
            if message.chat.id == peer:
                matched = True
                break
        except Exception:
            pass

    if not matched and str(message.chat.username or "").lower() not in ["database_cineverse01", "database_cineverse", "cenahub01"] and message.chat.id not in DB_CHANNEL_IDS:
        return

    media = message.document or message.video
    if not media:
        return

    file_id = getattr(media, "file_id", None)
    if not file_id:
        return

    raw_title = getattr(media, "file_name", None) or message.caption or "Unknown"
    clean_title = raw_title.replace(".", " ").replace("_", " ").replace("-", " ")
    caption = str(message.caption or "").strip()
    file_size = getattr(media, "file_size", 0) or 0
    cid = message.chat.id

    movie_doc = {
        "file_id": file_id,
        "file_name": clean_title,
        "title": clean_title,
        "file_size": file_size,
        "caption": caption,
        "message_id": int(message.id),
        "channel_id": cid
    }
    await db.movies.update_one(
        {"file_id": file_id},
        {"$set": movie_doc},
        upsert=True
    )
    logger.info(f"[AUTO-INDEX] Saved '{clean_title}' from chat {cid} (ID: {message.id}) to MongoDB movies collection")

# ---------------------------------------------------------------------------
# STRICT GROUP ACCESS & MULTI-GROUP ANTI-SPAM MODERATOR (GROUP -2)
# ---------------------------------------------------------------------------
@app.on_chat_member_updated()
async def on_bot_chat_member_updated(client: Client, member_update: ChatMemberUpdated):
    try:
        me_id = await get_bot_id(client)
        new_member = member_update.new_chat_member
        old_member = member_update.old_chat_member
        chat = member_update.chat

        if not chat or not new_member or not new_member.user:
            return

        # 1. BOT ADDED / JOINED TO GROUP EVENT (AUTO-LEAVE GUARD)
        if new_member.user.id == me_id:
            was_not_in_chat = (
                old_member is None or 
                old_member.status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]
            )
            is_now_in_chat = new_member.status in [
                enums.ChatMemberStatus.MEMBER,
                enums.ChatMemberStatus.ADMINISTRATOR,
                enums.ChatMemberStatus.RESTRICTED
            ]

            if not (was_not_in_chat and is_now_in_chat):
                return

            adder = member_update.from_user
            adder_id = adder.id if adder else None
            chat_title = getattr(chat, 'title', '') or 'Unknown Chat'

            logger.info(f"Bot added to chat: '{chat_title}' (Chat ID: {chat.id}) by user {adder_id}")
            print(f"Bot added to chat: '{chat_title}' (Chat ID: {chat.id}) by user {adder_id}")

            # AUTO-LEAVE UNAUTHORIZED GROUPS:
            if not is_owner_or_admin(adder_id):
                logger.warning(
                    f"⚠️ Unauthorized group addition! Chat '{chat_title}' ({chat.id}) "
                    f"added by non-owner {adder_id}. Leaving immediately..."
                )
                try:
                    await client.send_message(
                        chat.id,
                        "⚠️ Unauthorized group addition! Only the Owner can add this bot."
                    )
                except Exception as send_err:
                    logger.warning(f"Could not send warning message before leaving chat {chat.id}: {send_err}")

                try:
                    await client.leave_chat(chat.id)
                    logger.warning(f"🚪 Auto-left unauthorized chat: '{chat_title}' ({chat.id})")
                    print(f"🚪 Auto-left unauthorized chat: '{chat_title}' ({chat.id})")
                except Exception as leave_err:
                    logger.error(f"Failed to leave chat {chat.id}: {leave_err}")
                return
            else:
                logger.info(f"✅ Bot added to '{chat_title}' ({chat.id}) by authorized Owner/Admin {adder_id}")
                return

        # 2. MEMBER ADD COUNTER IN MAIN GROUP (COOLDOWN BYPASS TASK)
        is_main_group = (
            chat.id == MAIN_GROUP_ID or
            str(chat.id) == str(MAIN_GROUP_ID) or
            chat.id == SEARCH_GROUP_ID or
            str(chat.id) == str(SEARCH_GROUP_ID)
        )
        if is_main_group and not new_member.user.is_bot:
            was_not_in_chat = (
                old_member is None or
                old_member.status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]
            )
            is_now_in_chat = new_member.status in [
                enums.ChatMemberStatus.MEMBER,
                enums.ChatMemberStatus.RESTRICTED
            ]
            if was_not_in_chat and is_now_in_chat:
                adder = member_update.from_user
                if adder and adder.id != new_member.user.id and not adder.is_bot:
                    is_new, total_added, credits, new_credit_earned = await db.record_member_add(
                        adder_id=adder.id,
                        member_id=new_member.user.id,
                        chat_id=chat.id
                    )
                    if is_new:
                        logger.info(f"User {adder.id} added new member {new_member.user.id} to main group (Total added: {total_added})")
                        if new_credit_earned:
                            try:
                                await client.send_message(
                                    chat_id=adder.id,
                                    text=(
                                        "🎉 **Cooldown Bypass Unlocked!**\n\n"
                                        f"You successfully added **{total_added} members** to CineVerse Group!\n"
                                        "🎁 **+1 Instant File Download Credit has been granted!**\n"
                                        "⚡ Your active 7-hour Cooldown has been cleared.\n\n"
                                        "🍿 You can now download your movie immediately!"
                                    )
                                )
                            except Exception as notify_err:
                                logger.debug(f"Could not notify user {adder.id} in PM: {notify_err}")
    except Exception as e:
        logger.debug(f"Chat member update error: {e}")

@app.on_message(filters.group & filters.new_chat_members)
async def on_new_chat_members_added(client: Client, message: Message):
    try:
        chat = message.chat
        if not chat:
            return
        is_main_group = (
            chat.id == MAIN_GROUP_ID or
            str(chat.id) == str(MAIN_GROUP_ID) or
            chat.id == SEARCH_GROUP_ID or
            str(chat.id) == str(SEARCH_GROUP_ID)
        )
        if not is_main_group:
            return

        adder = message.from_user
        if not adder or adder.is_bot:
            return

        for new_user in message.new_chat_members:
            if new_user.is_bot or new_user.id == adder.id:
                continue
            is_new, total_added, credits, new_credit_earned = await db.record_member_add(
                adder_id=adder.id,
                member_id=new_user.id,
                chat_id=chat.id
            )
            if is_new and new_credit_earned:
                try:
                    await client.send_message(
                        chat_id=adder.id,
                        text=(
                            "🎉 **Cooldown Bypass Unlocked!**\n\n"
                            f"You successfully added **{total_added} members** to CineVerse Group!\n"
                            "🎁 **+1 Instant File Download Credit has been granted!**\n"
                            "⚡ Your active 7-hour Cooldown has been cleared.\n\n"
                            "🍿 You can now download your movie immediately!"
                        )
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Error handling new_chat_members: {e}")

@app.on_message(filters.group, group=-2)
async def group_security_and_antispam_handler(client: Client, message: Message):
    if message.from_user and (message.from_user.id in [7831101047, 783110149] or str(message.from_user.id) in ["7831101047", "783110149"]):
        return  # Skip subscription verification completely & bypass moderation for owner
    if not message.chat or message.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return

    # Log incoming chat ID for diagnostic inspection
    logger.info(f"Group message received from Chat ID: {message.chat.id} | Title: '{getattr(message.chat, 'title', '')}'")

    # 2. Multi-Group Anti-Spam (ONLY in REQUEST_GROUP_ID & BACKUP_GROUP_ID; NEVER in MAIN_GROUP_ID)
    if message.chat.id in [REQUEST_GROUP_ID, BACKUP_GROUP_ID]:
        if not message.from_user or message.from_user.is_bot:
            return
        user_id = message.from_user.id
        if not await is_sender_chat_admin(client, message.chat.id, user_id):
            text = (message.text or message.caption or "").strip()
            has_link = bool(
                re.search(r"(https?://\S+|t\.me/\S+|telegram\.me/\S+|www\.\S+)", text, re.IGNORECASE) or
                (message.entities and any(e.type in [enums.MessageEntityType.URL, enums.MessageEntityType.TEXT_LINK] for e in message.entities)) or
                (message.caption_entities and any(e.type in [enums.MessageEntityType.URL, enums.MessageEntityType.TEXT_LINK] for e in message.caption_entities))
            )
            has_bad_word = bool(SPAM_WORDS_PATTERN.search(text))
            if has_link or has_bad_word:
                try:
                    await message.delete()
                except Exception:
                    pass
                warn_count = await db.increment_user_warning(user_id, message.chat.id)
                user_mention = message.from_user.mention if message.from_user else f"User {user_id}"
                if warn_count < 3:
                    warn_text = (
                        "┌──────────────────────────────────────────┐\n"
                        "│   ⚠️  S P A M   W A R N I N G            │\n"
                        "└──────────────────────────────────────────┘\n\n"
                        f"👤 **User:** {user_mention}\n"
                        f"⚠️ **Warning:** `[{warn_count}/3]`\n"
                        "🚫 **Reason:** External links and abusive words are strictly prohibited in this group!\n\n"
                        "⚡ *You will be banned automatically on your 3rd warning (3/3).*"
                    )
                    try:
                        w_msg = await client.send_message(message.chat.id, warn_text)
                        if w_msg:
                            asyncio.create_task(auto_delete(w_msg, 60))
                    except Exception:
                        pass
                else:
                    try:
                        await client.ban_chat_member(message.chat.id, user_id)
                    except Exception as e:
                        logger.error(f"Failed to ban user {user_id} in {message.chat.id}: {e}")
                    await db.reset_user_warnings(user_id, message.chat.id)
                    ban_text = (
                        "┌──────────────────────────────────────────┐\n"
                        "│   🚫  U S E R   B A N N E D              │\n"
                        "└──────────────────────────────────────────┘\n\n"
                        f"👤 **User:** {user_mention} reached **[3/3] warnings**.\n"
                        "⛔ **Action:** Automatically banned from this group for repeated spam/abuse."
                    )
                    try:
                        b_msg = await client.send_message(message.chat.id, ban_text)
                        if b_msg:
                            asyncio.create_task(auto_delete(b_msg, 60))
                    except Exception:
                        pass
                message.stop_propagation()
                return

# ---------------------------------------------------------------------------
# GLOBAL INCOMING USER MESSAGE AUTO-DELETE TRACKER (GROUP -1)
# ---------------------------------------------------------------------------
@app.on_message(group=-1)
async def incoming_user_message_tracker(client: Client, message: Message):
    """
    Global 60-Second Auto-Delete Engine:
    Guarantees EVERY incoming user message in Group or PM (user search query,
    /start, /index, /stats commands, invalid queries, etc.) is automatically
    scheduled for deletion after exactly 60 seconds (1 minute).
    Excludes the DB Channel so channel archives are never removed.
    """
    try:
        peer = await resolve_channel_peer(client)
        # Never auto-delete media/messages inside the DB Channel
        if message.chat and (
            message.chat.id == peer or 
            str(getattr(message.chat, "username", "") or "").lower() in ["cenahub01", "@cenahub01"]
        ):
            return

        # Do not process outgoing messages or messages from bots
        if getattr(message, "outgoing", False) or (message.from_user and message.from_user.is_bot):
            return

        # Unauthorized Group Protection: If message is in an unauthorized group, check & leave immediately
        if message.chat and message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            if not await check_group_authorization(client, message):
                return

        # Auto-delete incoming user messages in Private Chats and Groups
        if message.chat and message.chat.type in [
            enums.ChatType.PRIVATE,
            enums.ChatType.GROUP,
            enums.ChatType.SUPERGROUP
        ]:
            msg_text = (message.text or message.caption or "").strip()
            asyncio.create_task(auto_delete(message, 60))
    except Exception as e:
        logger.debug(f"Tracker error: {e}")

# ---------------------------------------------------------------------------
# GROUP SERVICE MESSAGES CLEANER (AUTO-DELETE IMMEDIATELY)
# ---------------------------------------------------------------------------
@app.on_message(filters.service & ~filters.private)
async def service_message_cleaner(client: Client, message: Message):
    if is_duplicate_message(message):
        return

    # AUTO-LEAVE UNAUTHORIZED GROUPS:
    # If the bot is added to any group via service message by anyone OTHER THAN OWNER_ID/ADMIN_ID,
    # send warning and immediately leave the chat.
    if getattr(message, "new_chat_members", None):
        me_id = await get_bot_id(client)
        if any(u.id == me_id for u in message.new_chat_members):
            adder = message.from_user
            adder_id = adder.id if adder else None
            chat = message.chat
            chat_title = getattr(chat, 'title', '') or 'Unknown Chat'

            if not is_owner_or_admin(adder_id):
                logger.warning(
                    f"⚠️ Unauthorized group addition via service message! Chat '{chat_title}' ({chat.id}) "
                    f"added by non-owner {adder_id}. Leaving immediately..."
                )
                try:
                    await message.reply_text("⚠️ Unauthorized group addition! Only the Owner can add this bot.")
                except Exception:
                    try:
                        await client.send_message(
                            chat.id,
                            "⚠️ Unauthorized group addition! Only the Owner can add this bot."
                        )
                    except Exception as send_err:
                        logger.warning(f"Could not send warning message before leaving chat {chat.id}: {send_err}")

                try:
                    await client.leave_chat(chat.id)
                    logger.warning(f"🚪 Auto-left unauthorized chat: '{chat_title}' ({chat.id})")
                    print(f"🚪 Auto-left unauthorized chat: '{chat_title}' ({chat.id})")
                except Exception as leave_err:
                    logger.error(f"Failed to leave chat {chat.id}: {leave_err}")
                return
            else:
                logger.info(f"✅ Bot added to '{chat_title}' ({chat.id}) by authorized Owner/Admin {adder_id}")

    asyncio.create_task(auto_delete(message, 60))

# ---------------------------------------------------------------------------
# SEARCH & AUTO-FILTER HANDLER (WITH 60S AUTO-DELETE & SINGLE-PROCESS DEDUPLICATION)
# ---------------------------------------------------------------------------
@app.on_message((filters.group | filters.private) & filters.text & ~filters.bot & ~filters.service)
async def auto_filter_handler(client: Client, message: Message):
    if message.from_user and (message.from_user.id in [7831101047, 783110149] or str(message.from_user.id) in ["7831101047", "783110149"]):
        # Skip subscription verification completely for owner
        pass
    text = message.text.strip() if message.text else ""
    if not text:
        return

    if is_duplicate_message(message):
        return

    if not message.from_user:
        return
    user_id = message.from_user.id
    is_owner = is_owner_or_admin(user_id)

    # Ignore command messages so dedicated command handlers can process them
    if text.startswith("/"):
        return

    # 1. PM RESTRICTION: Disable search handling in private PM chat and redirect user to main CineVerse group
    if message.chat and message.chat.type == enums.ChatType.PRIVATE:
        pm_notice = (
            "┌──────────────────────────────────────────┐\n"
            "│   🍿  C I N E V E R S E   S E A R C H    │\n"
            "│          Search Restricted to Group      │\n"
            "└──────────────────────────────────────────┘\n\n"
            f"👋 Hello **{message.from_user.first_name or 'Friend'}**!\n\n"
            "◈ **Movie Search is only available in our Main Group!**\n"
            "├─ 💬 Movie queries are disabled in private PM chat.\n"
            "├─ 🔍 Please tap the button below to join & search in our group.\n"
            "└─ ⚡ Instant results & high-speed downloads guaranteed!\n\n"
            "👉 **Send your query in CineVerse Group:**"
        )
        group_button = InlineKeyboardMarkup([
            [InlineKeyboardButton("🍿 Search in CineVerse Group ↗", url=MAIN_GROUP_LINK)],
            [InlineKeyboardButton("📢 Official Channel ↗", url=BACKUP_CHANNEL_LINK)]
        ])
        pm_msg = await message.reply_text(pm_notice, reply_markup=group_button, quote=True)
        if pm_msg:
            asyncio.create_task(auto_delete(pm_msg, 60, reply_to_message=message))
        return

    # 2. STRICT MAIN GROUP SEARCH ONLY:
    # Restrict search query execution exclusively to the configured MAIN_GROUP_ID.
    # Completely ignore or block search functionality in database channels, request groups, or any unauthorized group/channel.
    is_main_search_chat = (
        message.chat and (
            message.chat.id == MAIN_GROUP_ID or 
            str(message.chat.id) == str(MAIN_GROUP_ID)
        )
    )
    if not is_main_search_chat:
        logger.info(
            f"🚫 [SEARCH BLOCKED] Search query '{text}' from user {user_id} ignored: "
            f"Chat {getattr(message.chat, 'id', None)} ('{getattr(message.chat, 'title', '')}') "
            f"is NOT configured MAIN_GROUP_ID ({MAIN_GROUP_ID})."
        )
        return

    # Print / Log incoming message in Main Group
    logger.info(f"Incoming Main Group Search: chat {message.chat.id} ('{getattr(message.chat, 'title', '')}') | User: {user_id} | Query: '{text}'")
    print(f"Incoming Main Group Search: chat {message.chat.id} | Query: '{text}'")

    # Enforce 60-second auto-delete on user search query message in Main Group
    asyncio.create_task(auto_delete(message, 60))

    # Code-Level Deduplication Lock on search query within 5 seconds
    search_lock_key = f"search_{message.chat.id}_{text.lower()}"
    if is_duplicate_request(search_lock_key, expiry=REQUEST_LOCK_EXPIRY):
        return

    await db.add_user(user_id)

    # 2. Owner Permission Gate: Strictly block non-approved / disapproved users
    if not is_owner and await db.is_disapproved_user(user_id):
        denied_msg = await message.reply_text(
            "┌──────────────────────────────────────────┐\n"
            "│   ❌  A C C E S S   D E N I E D          │\n"
            "└──────────────────────────────────────────┘\n\n"
            "◈ Authorization Notice:\n"
            "├─ 🚫 Access revoked or pending approval.\n"
            "└─ 👑 Required authorization from Owner Banty.",
            quote=True
        )
        if denied_msg:
            asyncio.create_task(auto_delete(denied_msg, 60, reply_to_message=message))
        return

    # Clean query (q = message.text.strip().lower())
    q = message.text.strip().lower()

    # 4. Search Query Validation: Handle queries < 2 chars as invalid queries
    if len(q) < 2:
        short_msg = await message.reply_text(
            "┌──────────────────────────────────────────┐\n"
            "│   ⚠️  I N V A L I D   Q U E R Y          │\n"
            "└──────────────────────────────────────────┘\n\n"
            "◈ **Search Query Too Short!**\n"
            "├─ Please enter at least 2 characters to search.\n"
            "└─ Example: `Avatar` or `Inception`",
            quote=True
        )
        if short_msg:
            asyncio.create_task(auto_delete(short_msg, 60, reply_to_message=message))
        return

    # 1. CHECK DB COUNT AT SEARCH TIME:
    # Inside the search query handler, before querying, print/log movies count
    db_movie_count = 0
    try:
        db_movie_count = await db.movies.count_documents({})
    except Exception as count_err:
        logger.error(f"Error checking movies collection count at search time: {count_err}")

    logger.info(f"📊 [SEARCH-COUNT-CHECK] Current database movies count: {db_movie_count} | Query: '{q}' | User ID: {user_id}")
    print(f"📊 [SEARCH-COUNT-CHECK] Current database movies count: {db_movie_count} | Query: '{q}' | User ID: {user_id}")

    # If count is 0, auto-trigger a background re-index or log a critical notice so database state is clear.
    if db_movie_count == 0:
        logger.critical(
            f"🚨 [CRITICAL NOTICE] Movies collection is completely EMPTY (COUNT = 0) at search time for query '{q}'! "
            f"Auto-triggering background re-index from target channels (@database_Cineverse01, @database_Cineverse)..."
        )
        print(
            f"🚨 [CRITICAL NOTICE] Movies collection is completely EMPTY (COUNT = 0) at search time for query '{q}'! "
            f"Auto-triggering background re-index from target channels (@database_Cineverse01, @database_Cineverse)..."
        )
        asyncio.create_task(_async_channel_indexer_task(client, message, None, ["@database_Cineverse01", "@database_Cineverse"]))

    try:
        # Multi-word regex case-insensitive search on file_name and caption:
        query = q
        search_filter = {"$or": [{"file_name": {"$regex": query, "$options": "i"}}, {"caption": {"$regex": query, "$options": "i"}}]}

        raw_docs = []
        total = 0
        try:
            total = await db.movies.count_documents(search_filter)
            if total > 0:
                cursor = db.movies.find(search_filter).limit(PAGE_SIZE)
                raw_docs = await cursor.to_list(length=PAGE_SIZE)
        except Exception:
            safe_query = re.escape(query)
            search_filter = {"$or": [{"file_name": {"$regex": safe_query, "$options": "i"}}, {"caption": {"$regex": safe_query, "$options": "i"}}]}
            try:
                total = await db.movies.count_documents(search_filter)
                if total > 0:
                    cursor = db.movies.find(search_filter).limit(PAGE_SIZE)
                    raw_docs = await cursor.to_list(length=PAGE_SIZE)
            except Exception:
                total = 0
                raw_docs = []

        words = q.split()
        # In-order multi-word regex search (matching tokens separated by dots, spaces, dashes)
        if total == 0 and len(words) > 1:
            multi_regex = ".*".join(re.escape(w) for w in words)
            mw_filter = {"$or": [{"file_name": {"$regex": multi_regex, "$options": "i"}}, {"caption": {"$regex": multi_regex, "$options": "i"}}]}
            try:
                total = await db.movies.count_documents(mw_filter)
                if total > 0:
                    cursor = db.movies.find(mw_filter).limit(PAGE_SIZE)
                    raw_docs = await cursor.to_list(length=PAGE_SIZE)
            except Exception:
                pass

        # Word-by-word $and fallback if still no results
        if total == 0 and words:
            word_conditions = []
            for w in words:
                safe_w = re.escape(w)
                word_conditions.append({
                    "$or": [
                        {"file_name": {"$regex": safe_w, "$options": "i"}},
                        {"caption": {"$regex": safe_w, "$options": "i"}}
                    ]
                })
            fallback_filter = {"$and": word_conditions}
            try:
                total = await db.movies.count_documents(fallback_filter)
                if total > 0:
                    cursor = db.movies.find(fallback_filter).limit(PAGE_SIZE)
                    raw_docs = await cursor.to_list(length=PAGE_SIZE)
            except Exception:
                raw_docs = []
                total = 0

        files = []
        for d in raw_docs:
            f_id = str(d.get("file_id") or "")
            f_name = str(d.get("file_name") or d.get("title") or "Movie")
            f_size = int(d.get("file_size") or 0)
            cap = str(d.get("caption") or "")
            m_id = d.get("message_id")
            cid = d.get("channel_id") or DB_CHANNEL_ID

            files.append({
                "file_id": f_id,
                "title": f_name,
                "file_name": f_name,
                "file_size": f_size,
                "caption": cap,
                "message_id": m_id or f_id,
                "channel_id": cid
            })

        logger.info(f"Database search for '{q}' in chat {message.chat.id} returned {total} results")
        if total == 0 or not files:
            # On zero search results, send request alert directly to @FlimRequest_01
            try:
                user_first = message.from_user.first_name if (message.from_user and message.from_user.first_name) else "User"
                user_uname = f"@{message.from_user.username}" if (message.from_user and message.from_user.username) else "No Username"
                user_uid = message.from_user.id if message.from_user else message.chat.id
                req_alert = (
                    "📩 New Movie Request\n"
                    f"👤 User: {user_first} ({user_uname})\n"
                    f"🎬 Movie Name: {text}\n"
                    f"🆔 User ID: {user_uid}"
                )
                try:
                    await client.send_message("@FlimRequest_01", req_alert)
                    logger.info(f"✅ Sent request alert for '{text}' to @FlimRequest_01")
                except Exception as send_err:
                    logger.warning(f"send_message to @FlimRequest_01 failed: {send_err}, attempting forward...")
                    await message.forward("@FlimRequest_01")
            except Exception as fwd_err:
                logger.warning(f"Could not send request alert to @FlimRequest_01: {fwd_err}")

            not_found_markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📝 Request This Movie", url=REQUEST_CHANNEL_LINK)
                ],
                [
                    InlineKeyboardButton("📢 Official Channel ↗", url=FSUB_CHANNEL_LINK)
                ]
            ])
            not_found_msg = await message.reply_text(
                "┌──────────────────────────────────────────┐\n"
                "│   ❌  F I L E   N O T   F O U N D        │\n"
                "└──────────────────────────────────────────┘\n\n"
                f"◈ **Searched:** `{text}`\n"
                "├─ ⚠️ No matching file available in current vault.\n"
                "├─ 📝 Tap below to post your request in Movie Request Group!\n"
                "├─ 💡 Tip: Check spelling or try searching with release year.\n"
                f"└─ 🍿 Official Channel: {FSUB_CHANNEL_LINK}",
                reply_markup=not_found_markup,
                quote=True,
                disable_web_page_preview=True
            )
            if not_found_msg:
                asyncio.create_task(auto_delete(not_found_msg, 60, reply_to_message=message))
            return

        cache_id = uuid.uuid4().hex[:8]
        SEARCH_CACHE[cache_id] = text
        total_pages = math.ceil(total / PAGE_SIZE) if total > PAGE_SIZE else 1
        bot_uname = await get_bot_username(client)
        markup = await build_pagination_markup(files, cache_id, 1, total_pages, bot_username=bot_uname)
        response_text = format_search_text(text, total, 1, total_pages)

        # Reply directly (Single message only, NO duplicate notices)
        sent_msg = await message.reply_text(response_text, reply_markup=markup, quote=True)
        if sent_msg:
            asyncio.create_task(auto_delete(sent_msg, 60, reply_to_message=message))
    except Exception as e:
        logger.error(f"❌ Database/Search error processing '{text}' in chat {message.chat.id}: {e}", exc_info=True)
        print(f"❌ Database/Search error for '{text}' in chat {message.chat.id}: {e}")
        try:
            fallback_markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📝 Request This Movie", url=REQUEST_CHANNEL_LINK)
                ],
                [
                    InlineKeyboardButton("📢 Official Channel ↗", url=FSUB_CHANNEL_LINK)
                ]
            ])
            fallback_msg = await message.reply_text(
                "┌──────────────────────────────────────────┐\n"
                "│   ❌  F I L E   N O T   F O U N D        │\n"
                "└──────────────────────────────────────────┘\n\n"
                f"◈ **Searched:** `{text}`\n"
                "├─ ⚠️ No matching file available in current vault.\n"
                "├─ 📝 Tap below to post your request in Movie Request Group!\n"
                "├─ 💡 Tip: Check spelling or try searching with release year.\n"
                f"└─ 🍿 Official Channel: {FSUB_CHANNEL_LINK}",
                reply_markup=fallback_markup,
                quote=True,
                disable_web_page_preview=True
            )
            if fallback_msg:
                asyncio.create_task(auto_delete(fallback_msg, 60, reply_to_message=message))
        except Exception:
            pass

# ---------------------------------------------------------------------------
# CALLBACK QUERY ROUTER
# ---------------------------------------------------------------------------
@app.on_callback_query()
async def callback_router(client: Client, query: CallbackQuery):
    if query.from_user and (query.from_user.id in [7831101047, 783110149] or str(query.from_user.id) in ["7831101047", "783110149"]):
        # Skip subscription verification completely for owner
        pass
    if not query.from_user:
        return
    user_id = query.from_user.id
    is_owner = (user_id in [7831101047, 783110149] or str(user_id) in ["7831101047", "783110149"])

    # Code-Level Deduplication Lock on callback queries & callback data within 5 seconds
    if is_duplicate_callback(query, expiry=REQUEST_LOCK_EXPIRY):
        try:
            await query.answer()
        except Exception:
            pass
        return

    await db.add_user(user_id)

    # 1. Owner Permission Gate: Check if disapproved
    if not is_owner and await db.is_disapproved_user(user_id):
        await query.answer("❌ Access Denied: Permission required from Owner Banty.", show_alert=True)
        return

    data = query.data

    if data == "cb_check_join":
        is_joined = is_owner or await is_user_fully_joined(client, user_id)
        if is_joined:
            await query.answer("✅ Verified! Access Granted.", show_alert=False)
            try:
                await query.message.edit_text(
                    "┌──────────────────────────────────────────┐\n"
                    "│   ✅  A C C E S S   G R A N T E D        │\n"
                    "│       Membership Verified & Unlocked     │\n"
                    "└──────────────────────────────────────────┘\n\n"
                    "🎉 **Welcome to CineVerse Search Network!**\n\n"
                    "Your membership in both official channels has been verified successfully.\n\n"
                    "◈ **Next Step:**\n"
                    "🍿 Search any movie title directly in CineVerse Group!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🍿 Go to CineVerse Group ↗", url=MAIN_GROUP_LINK)]
                    ])
                )
            except Exception:
                pass
        else:
            await query.answer("❌ You haven't joined BOTH channels yet! Please join and retry.", show_alert=True)
        return

    elif data == "cb_close":
        try:
            if getattr(query.message, "reply_to_message", None):
                try:
                    if not await is_message_pinned(query.message.reply_to_message, client=client):
                        await query.message.reply_to_message.delete()
                except Exception:
                    pass
            elif getattr(query.message, "reply_to_message_id", None) and query.message.chat:
                try:
                    pinned_id = await get_chat_pinned_message_id(client, query.message.chat.id)
                    if pinned_id is None or query.message.reply_to_message_id != pinned_id:
                        await client.delete_messages(chat_id=query.message.chat.id, message_ids=[query.message.reply_to_message_id])
                except Exception:
                    pass
            if not await is_message_pinned(query.message, client=client):
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

    elif data.startswith("flt_menu_") or data.startswith("flt_m_"):
        parts = data.split("_")
        menu_type = parts[2] if data.startswith("flt_menu_") else parts[2]
        cache_id = parts[3]
        search_query = SEARCH_CACHE.get(cache_id)

        if not search_query:
            await query.answer("⚠️ Search session expired. Please search again.", show_alert=True)
            return

        if menu_type in ["qual", "quality"]:
            menu_text = (
                "┌──────────────────────────────────────────┐\n"
                "│   📺  Q U A L I T Y   F I L T E R        │\n"
                "│        Select Preferred Resolution       │\n"
                "└──────────────────────────────────────────┘\n\n"
                f"◈ **Active Query:** `{search_query}`\n\n"
                "✨ *Choose a resolution badge below to filter:*"
            )
            markup = build_quality_menu(cache_id)
        elif menu_type in ["lang", "language"]:
            menu_text = (
                "┌──────────────────────────────────────────┐\n"
                "│   🌐  L A N G U A G E   F I L T E R      │\n"
                "│         Select Preferred Audio           │\n"
                "└──────────────────────────────────────────┘\n\n"
                f"◈ **Active Query:** `{search_query}`\n\n"
                "✨ *Choose an audio language badge below to filter:*"
            )
            markup = build_language_menu(cache_id)
        elif menu_type == "season":
            menu_text = (
                "┌──────────────────────────────────────────┐\n"
                "│   🎬  S E A S O N   F I L T E R          │\n"
                "│       Select Television Season           │\n"
                "└──────────────────────────────────────────┘\n\n"
                f"◈ **Active Query:** `{search_query}`\n\n"
                "✨ *Choose a television season badge below to filter:*"
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
        bot_uname = await get_bot_username(client)
        markup = await build_pagination_markup(files, new_cache_id, 1, total_pages, bot_username=bot_uname)
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

        bot_uname = await get_bot_username(client)
        markup = await build_pagination_markup(files, cache_id, 1, total_pages, bot_username=bot_uname)
        response_text = format_search_text(search_query, total, 1, total_pages)
        try:
            await query.message.edit_text(response_text, reply_markup=markup)
        except MessageNotModified:
            pass
        await query.answer()

    elif data == "cb_help":
        help_text = (
            "┌──────────────────────────────────────────┐\n"
            "│   📖  C I N E V E R S E   G U I D E      │\n"
            "│       How to Search and Download         │\n"
            "└──────────────────────────────────────────┘\n\n"
            "◈ **3-Step Quick Guide:**\n"
            "├─ 1️⃣ **Search:** Type any movie or series title in our search group.\n"
            "├─ 2️⃣ **Select:** Pick your desired resolution (4K, 1080p, 720p, HEVC).\n"
            "└─ 3️⃣ **Receive:** Bot delivers the file directly to your PM instantly!\n\n"
            "◈ **Command Center:**\n"
            "├─ `/start` ─ Initialize bot & access main portal\n"
            "├─ `/help` ─ Interactive usage guide\n"
            "├─ `/about` ─ About CineVerse & developer\n"
            "└─ `/dmca` ─ Content takedown request\n\n"
            "──────────────────────────────────────────\n"
            "⚡ *Powered by CineVerse Luxury Media Engine*"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Portal", callback_data="cb_home")]])
        try:
            await query.message.edit_caption(caption=help_text, reply_markup=markup)
        except Exception:
            await query.message.edit_text(help_text, reply_markup=markup)
        await query.answer()

    elif data == "cb_about":
        stats = await db.get_stats()
        about_text = (
            "┌──────────────────────────────────────────┐\n"
            "│   💎  A B O U T   C I N E V E R S E      │\n"
            "│       Next-Gen Media Discovery Bot       │\n"
            "└──────────────────────────────────────────┘\n\n"
            "◈ **System Overview:**\n"
            "├─ 🤖 Bot: `CineVerse Auto-Filter Engine`\n"
            f"├─ 📁 Total Vault Titles: `{stats['movies']}`\n"
            f"├─ 👥 Total Registered Users: `{stats['users']}`\n"
            "├─ ⚡ Architecture: `Ultra-Fast Async MongoDB (Motor)`\n"
            "├─ 👑 Developer: `Banty`\n"
            "└─ ✨ Network: **CineVerse Cinematic Network**\n\n"
            "──────────────────────────────────────────\n"
            "⚡ *Engineered for Perfection & Pure Speed*"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Portal", callback_data="cb_home")]])
        try:
            await query.message.edit_caption(caption=about_text, reply_markup=markup)
        except Exception:
            await query.message.edit_text(about_text, reply_markup=markup)
        await query.answer()

    elif data == "cb_disclaimer":
        disclaimer_text = (
            "┌──────────────────────────────────────────┐\n"
            "│   ⚖️  L E G A L   D I S C L A I M E R    │\n"
            "│        Terms of Service & Hosting        │\n"
            "└──────────────────────────────────────────┘\n\n"
            "◈ **Compliance Overview:**\n"
            "├─ 🚫 **Non-Hosting Policy:** This service does NOT host, store, or upload any video files on its servers.\n"
            "├─ 🔍 **Automated Indexing:** All media metadata is indexed automatically from public third-party Telegram channels.\n"
            "└─ 🛡️ **Copyright Respect:** CineVerse honors intellectual property rights. To delist any content, use `/dmca`.\n\n"
            "──────────────────────────────────────────\n"
            "⚡ *CineVerse Network Legal Department*"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Portal", callback_data="cb_home")]])
        try:
            await query.message.edit_caption(caption=disclaimer_text, reply_markup=markup)
        except Exception:
            await query.message.edit_text(disclaimer_text, reply_markup=markup)
        await query.answer()

    elif data == "cb_dmca":
        dmca_text = (
            "┌──────────────────────────────────────────┐\n"
            "│   📋  D M C A   T A K E D O W N          │\n"
            "│       Copyright Protection Portal        │\n"
            "└──────────────────────────────────────────┘\n\n"
            "◈ **Content Removal Procedure:**\n"
            "├─ If you are a copyright owner or authorized representative, submit details:\n"
            "├─ **Format:** `/dmca <Movie Title, Details, or Link>`\n"
            "├─ **Example:** `/dmca Please remove 'Movie Title (2024)' from search index.`\n"
            "└─ All requests are forwarded directly to the administrator for immediate action.\n\n"
            "──────────────────────────────────────────\n"
            "⚡ *CineVerse Content Integrity Team*"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Portal", callback_data="cb_home")]])
        try:
            await query.message.edit_caption(caption=dmca_text, reply_markup=markup)
        except Exception:
            await query.message.edit_text(dmca_text, reply_markup=markup)
        await query.answer()

    elif data == "cb_rules":
        rules_text = (
            "┌──────────────────────────────────────────┐\n"
            "│   📜  C I N E V E R S E   R U L E S      │\n"
            "│    Community & Downloading Guidelines    │\n"
            "└──────────────────────────────────────────┘\n\n"
            "🇬🇧 **English Guidelines:**\n"
            "├─ 1. Send clean movie / series names only (e.g. `Avatar` or `Leo`).\n"
            "├─ 2. Strictly 1 File = 1 Ad verification to keep high-speed vault free.\n"
            "├─ 3. No spamming, abusive language, or non-movie links.\n"
            "└─ 4. Auto-delete is active (60 seconds) to maintain chat cleanliness.\n\n"
            "🇮🇳 **ଓଡ଼ିଆ ନିୟମାବଳୀ (Odia Guidelines):**\n"
            "├─ ୧. ଗ୍ରୁପ୍‌ରେ କେବଳ ଫିଲ୍ମ କିମ୍ବା ୱେବ୍ ସିରିଜ୍‌ର ସଠିକ୍ ନାମ ଲେଖି ସର୍ଚ୍ଚ କରନ୍ତୁ।\n"
            "├─ ୨. ଡାଉନଲୋଡ୍ କରିବା ପାଇଁ ଗୋଟିଏ ଫାଇଲ୍ ପାଇଁ ଗୋଟିଏ ଆଡ୍ ଭେରିଫିକେସନ୍ ବାଧ୍ୟତାମୂଳକ।\n"
            "├─ ୩. କୌଣସି ଅପ୍ରାସଙ୍ଗିକ ଲିଙ୍କ୍ କିମ୍ବା ଗାଳିଗୁଲଜ ବ୍ୟବହାର କରନ୍ତୁ ନାହିଁ।\n"
            "└─ ୪. ସମସ୍ତ ମେସେଜ୍ ୬୦ ସେକେଣ୍ଡ ମଧ୍ୟରେ ସ୍ୱତଃ ଡିଲିଟ୍ ହୋଇଯିବ।\n\n"
            "🇮🇳 **हिंदी नियमावली (Hindi Guidelines):**\n"
            "├─ १. सर्च ग्रुप में केवल सही मूवी अथवा सीरीज का नाम लिखकर खोजें।\n"
            "├─ २. फ्री हाई-स्पीड डाउनलोड के लिए प्रति फ़ाइल १-पेज ऐड वेरिफिकेशन अनिवार्य है।\n"
            "├─ ३. ग्रुप में स्पैम, अभद्र भाषा या अन्य लिंक शेयर करना सख्त मना है।\n"
            "└─ ४. चैट साफ़ रखने के लिए सभी संदेश ६० सेकंड में स्वतः डिलीट हो जाते हैं।\n\n"
            "──────────────────────────────────────────\n"
            "⚡ *CineVerse Network Policy Team*"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Main Portal", callback_data="cb_home")]])
        try:
            await query.message.edit_caption(caption=rules_text, reply_markup=markup)
        except Exception:
            await query.message.edit_text(rules_text, reply_markup=markup)
        await query.answer()

    elif data == "cb_home":
        stats = await db.get_stats()
        bot_me = await client.get_me()
        user_mention = query.from_user.mention if query.from_user else "Friend"
        caption, markup = build_main_menu_card(user_mention, stats, bot_me.username)
        try:
            await query.message.edit_caption(caption=caption, reply_markup=markup)
        except Exception:
            try:
                await query.message.edit_text(caption, reply_markup=markup)
            except MessageNotModified:
                pass
        await query.answer()

    elif data.startswith("get_"):
        raw_id = data.split("_", 1)[1]
        msg_id = int(raw_id) if raw_id.isdigit() else raw_id

        # Strict Force Join Check (Request Channel & Backup Channel)
        if not is_owner and not await is_user_fully_joined(client, user_id):
            await query.answer("❌ You must join BOTH channels first!", show_alert=True)
            user_name = query.from_user.first_name if query.from_user else "Friend"
            fsub_text = build_force_sub_text(user_name)
            fsub_markup = build_force_join_markup(f"get_{msg_id}")
            try:
                block_msg = await query.message.reply_text(
                    fsub_text,
                    reply_markup=fsub_markup,
                    quote=True,
                    disable_web_page_preview=True
                )
                if block_msg:
                    asyncio.create_task(auto_delete(block_msg, 60, reply_to_message=query.message))
            except Exception:
                pass
            return

        # Code-Level Deduplication Lock for file delivery callbacks
        delivery_key = f"deliver_{user_id}_{msg_id}"
        if is_duplicate_request(delivery_key, expiry=REQUEST_LOCK_EXPIRY):
            await query.answer("⏳ Request is already being processed!", show_alert=False)
            return

        # 7-Hour Rate Limit per User (25200 seconds):
        now = time.time()
        last_dl = await db.get_user_last_download(user_id)
        elapsed = now - last_dl
        if not (user_id == OWNER_ID or user_id in ADMIN_IDS) and elapsed < FILE_COOLDOWN_SECONDS:
            credits = await db.get_user_credits(user_id)
            if credits > 0:
                await db.consume_user_credit(user_id)
                await db.set_user_last_download(user_id, now)
                await query.answer("🎟 Instant Download Credit applied! Delivering file...", show_alert=False)
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await deliver_movie_file(client, user_id, msg_id)
                return

            remaining = int(FILE_COOLDOWN_SECONDS - elapsed)
            rem_hours = remaining // 3600
            rem_mins = (remaining % 3600) // 60
            cooldown_text = (
                "┌──────────────────────────────────────────┐\n"
                "│   ⚠️  C O O L D O W N   A C T I V E      │\n"
                "└──────────────────────────────────────────┘\n\n"
                f"⚠️ Cooldown Active! You can only request 1 file every 7 hours. Please wait {rem_hours} hours {rem_mins} minutes.\n\n"
                "🍿 Search again later in CineVerse Search Group!\n\n"
                "💡 **Don't want to wait 7 hours?** Unlock instant download right now!"
            )
            try:
                await query.message.edit_text(
                    cooldown_text,
                    reply_markup=build_cooldown_markup(msg_id)
                )
            except Exception:
                await query.answer(f"⚠️ Cooldown Active! Please wait {rem_hours}h {rem_mins}m or unlock via Bypass task.", show_alert=True)
            return

        # Direct Delivery (No Ads): Deliver movie file instantly
        await db.set_user_last_download(user_id, now)
        await query.answer("✅ Delivering your file...", show_alert=False)
        try:
            await query.message.delete()
        except Exception:
            pass
        await deliver_movie_file(client, user_id, msg_id)
        return

    elif data.startswith("bypass_"):
        file_target = data[7:].strip()
        added_count = await db.get_user_added_members_count(user_id)
        credits = await db.get_user_credits(user_id)
        bot_uname = await get_bot_username(client)
        card_text, card_markup = build_bypass_task_card(
            user_id=user_id,
            added_count=added_count,
            credits=credits,
            file_msg_id=file_target,
            bot_uname=bot_uname
        )
        try:
            await query.message.edit_text(
                card_text,
                reply_markup=card_markup,
                disable_web_page_preview=True
            )
        except Exception:
            try:
                await query.message.reply_text(
                    card_text,
                    reply_markup=card_markup,
                    disable_web_page_preview=True
                )
            except Exception:
                pass
        await query.answer()
        return

    elif data.startswith("verify_bypass_") or data == "verify_bypass":
        file_target = data.replace("verify_bypass_", "").replace("verify_bypass", "").strip()
        target_msg_id = int(file_target) if file_target.isdigit() else None

        added_count = await db.get_user_added_members_count(user_id)
        credits = await db.get_user_credits(user_id)
        now = time.time()
        last_dl = await db.get_user_last_download(user_id)
        elapsed = now - last_dl
        is_cooldown_active = not (user_id == OWNER_ID or user_id in ADMIN_IDS) and elapsed < FILE_COOLDOWN_SECONDS

        if credits > 0:
            await db.consume_user_credit(user_id)
            await db.clear_user_cooldown(user_id)
            await query.answer("🎉 Verified! 1 Download Credit applied & Cooldown cleared!", show_alert=True)
            if target_msg_id:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await deliver_movie_file(client, user_id, target_msg_id)
                return
            else:
                bot_uname = await get_bot_username(client)
                card_text, card_markup = build_bypass_task_card(
                    user_id=user_id,
                    added_count=added_count,
                    credits=credits - 1,
                    file_msg_id=file_target,
                    bot_uname=bot_uname
                )
                try:
                    await query.message.edit_text(card_text, reply_markup=card_markup, disable_web_page_preview=True)
                except Exception:
                    pass
                return

        if not is_cooldown_active:
            await query.answer("✅ Cooldown is not active! Delivering your file...", show_alert=False)
            if target_msg_id:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await deliver_movie_file(client, user_id, target_msg_id)
                return
            else:
                return

        rem_in_cycle = added_count % 3
        needed = 3 - rem_in_cycle
        await query.answer(
            f"⚠️ Verification: You've added {added_count} member(s) ({rem_in_cycle}/3).\n"
            f"Please add {needed} more member(s) to unlock +1 download credit!",
            show_alert=True
        )
        bot_uname = await get_bot_username(client)
        card_text, card_markup = build_bypass_task_card(
            user_id=user_id,
            added_count=added_count,
            credits=credits,
            file_msg_id=file_target,
            bot_uname=bot_uname
        )
        try:
            await query.message.edit_text(card_text, reply_markup=card_markup, disable_web_page_preview=True)
        except Exception:
            pass
        return

    elif data.startswith("check_token_"):
        token = data.split("_", 2)[2]
        if not is_owner and not await is_user_fully_joined(client, user_id):
            await query.answer("❌ You must join both Request Group and Backup Channel first!", show_alert=True)
            return

        delivered_msg_id = await db.verify_and_consume_token(token, user_id)
        if delivered_msg_id:
            await query.answer("✅ Verified! Delivering your file...", show_alert=False)
            try:
                await query.message.delete()
            except Exception:
                pass
            await deliver_movie_file(client, user_id, delivered_msg_id)
            return
        elif await db.is_token_used(token):
            await query.answer("⚠️ This verification link has already delivered its file.", show_alert=True)
        else:
            await query.answer("❌ Invalid or expired verification link.", show_alert=True)
        return

    elif data in ["how_to_open_link", "ad_video_guide"]:
        popup_guide = (
            "⚡ 1-STEP VERIFICATION / କେମିତି ଖୋଲିବେ:\n"
            "📍 1. Tap 'Download / Get File'\n"
            "⏱️ 2. Wait 5-10s timer on page\n"
            "🔓 3. Click 'Get Link' -> File delivers in PM!\n"
            "ଲିଙ୍କ୍ ଖୋଲି ଅପେକ୍ଷା କରି Get Link ଦବାନ୍ତୁ!"
        )
        await query.answer(popup_guide, show_alert=True)

        guide_card = (
            "┌──────────────────────────────────────────┐\n"
            "│   🎬  1-STEP QUICK VERIFICATION GUIDE    │\n"
            "│          କେମିତି ଖୋଲିବେ? (How To Open)     │\n"
            "└──────────────────────────────────────────┘\n\n"
            "⚡ **How to Complete 1-Step Quick Verification:**\n\n"
            "📍 **Step 1: Open the Verification Link**\n"
            "├─ Tap `[ 📥 Download / Get File ]`\n"
            "└─ It opens the secure verification page in your browser.\n\n"
            "⏱️ **Step 2: Wait 5-10 Seconds Timer**\n"
            "├─ Scroll down slightly on the webpage.\n"
            "└─ Wait for the countdown timer to reach 0.\n\n"
            "🔓 **Step 3: Click Get Link**\n"
            "├─ Click the green **GET LINK** button.\n"
            "└─ You will automatically return to CineVerse Bot!\n\n"
            "🚀 **Instant Delivery:** Your requested movie file will be delivered directly here in PM!\n\n"
            "⏳ *Take your time — your link & messages are valid for 3 MINUTES!*"
        )
        try:
            guide_msg = await query.message.reply_text(
                guide_card,
                quote=True,
                disable_web_page_preview=True
            )
            if guide_msg:
                asyncio.create_task(auto_delete(guide_msg, 180, reply_to_message=query.message))
        except Exception as e:
            logger.debug(f"Guide reply error: {e}")
        return

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

        bot_uname = await get_bot_username(client)
        markup = await build_pagination_markup(files, cache_id, page, total_pages, bot_username=bot_uname)
        response_text = format_search_text(search_query, total, page, total_pages)
        try:
            await query.message.edit_text(response_text, reply_markup=markup)
        except MessageNotModified:
            pass
        await query.answer()

# ---------------------------------------------------------------------------
# BACKGROUND FLASK WEB SERVER (FOR RENDER HEALTH CHECKS & 24/7 ACTIVE STATUS)
# ---------------------------------------------------------------------------
flask_app = Flask("CineVerseHealthCheck") if Flask else None

if flask_app:
    # Disable flask/werkzeug request logging noise
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

    @flask_app.route("/")
    def index():
        if request and request.args.get("file_id") and send_from_directory:
            try:
                return send_from_directory(".", "verification.html")
            except Exception:
                pass
        return "OK - CineVerse Movie Search Bot is running 24/7!", 200

    @flask_app.route("/redirect")
    @flask_app.route("/verify")
    @flask_app.route("/verification")
    @flask_app.route("/verification.html")
    def verification_landing():
        if send_from_directory:
            try:
                return send_from_directory(".", "verification.html")
            except Exception as e:
                logger.error(f"Error serving verification.html: {e}")
        try:
            with open("verification.html", "r", encoding="utf-8") as f:
                return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}
        except Exception as e:
            return f"Error loading verification page: {e}", 500

    @flask_app.route("/health")
    def health():
        return "OK", 200

def start_flask_server():
    port_str = os.getenv("PORT", "8080")
    try:
        port = int(port_str)
    except (ValueError, TypeError):
        port = 8080

    def run_server():
        if flask_app:
            logger.info(f"🌐 Flask web server listening on 0.0.0.0:{port}")
            try:
                flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
            except Exception as e:
                logger.warning(f"Flask server error on port {port}: {e}")
        else:
            from http.server import HTTPServer, BaseHTTPRequestHandler
            class FallbackHandler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if any(self.path.startswith(p) for p in ["/verify", "/verification", "/verification.html", "/redirect"]):
                        try:
                            with open("verification.html", "rb") as f:
                                content = f.read()
                            self.send_response(200)
                            self.send_header("Content-type", "text/html; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(content)
                            return
                        except Exception:
                            pass
                    self.send_response(200)
                    self.send_header("Content-type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"OK - CineVerse Movie Search Bot is running 24/7!\n")
                def log_message(self, format, *args):
                    pass
            try:
                server = HTTPServer(("0.0.0.0", port), FallbackHandler)
                logger.info(f"🌐 Fallback HTTP server listening on 0.0.0.0:{port}")
                server.serve_forever()
            except Exception as e:
                logger.warning(f"HTTP server error on port {port}: {e}")

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

# ---------------------------------------------------------------------------
# START BOT WITH GRACEFUL FLOODWAIT RETRY ENGINE
# ---------------------------------------------------------------------------
async def set_bot_commands(client: Client):
    """
    Sets official bot commands:
    1. Default public scope (BotCommandScopeDefault): /start, /help, /about, /dmca.
    2. Owner scope (BotCommandScopeChat for 7831101047): /index, /stats, /start, /help, /about.
    """
    try:
        public_commands = [
            BotCommand("start", "Start Bot"),
            BotCommand("help", "Help Guide"),
            BotCommand("about", "About Bot"),
            BotCommand("dmca", "DMCA Takedown Notice"),
        ]
        await client.set_bot_commands(public_commands, scope=BotCommandScopeDefault())
        logger.info("✅ Public bot commands updated: /start, /help, /about, /dmca visible in default public menu")
    except Exception as e:
        logger.warning(f"Failed to set default bot commands: {e}")

    try:
        await client.set_bot_commands(
            [
                BotCommand("index", "Index Movies Database"),
                BotCommand("stats", "Bot Statistics"),
                BotCommand("start", "Start Bot"),
                BotCommand("help", "Help Guide"),
                BotCommand("about", "About CineVerse")
            ],
            scope=BotCommandScopeChat(chat_id=7831101047)
        )
        logger.info("✅ Owner-specific menu registered for chat 7831101047")
    except Exception as e:
        logger.warning(f"Could not set owner menu on startup for 7831101047: {e}")

async def _start_pyrogram_client():
    """
    Starts the Pyrogram client instance with graceful FloodWait backoff.
    Gracefully catches FloodWait or startup exceptions without crashing the deploy process.
    """
    while True:
        try:
            logger.info("📡 Connecting to Telegram via Pyrogram...")
            await app.start()
            logger.info("✅ Pyrogram bot client connected and authenticated successfully!")
            await db.init_indexes()
            await db.ensure_defaults()
            logger.info(f"✅ Connected to MongoDB Motor at '{db.uri_masked}' | Database: '{db.db_name}'")
            await set_bot_commands(app)
            # Resolve target channels as admin on startup
            for scid in TARGET_CHANNELS:
                try:
                    chat_ident = int(scid) if (isinstance(scid, int) or (isinstance(scid, str) and str(scid).lstrip("-").isdigit())) else str(scid).strip()
                    schat = await app.get_chat(chat_ident)
                    logger.info(f"✅ Resolved channel entity on startup: {getattr(schat, 'title', '')} ({schat.id})")
                except Exception as sc_err:
                    logger.warning(f"Could not resolve channel {scid} on startup: {sc_err}")
            break
        except FloodWait as e:
            wait_time = int(getattr(e, "value", getattr(e, "x", 60)))
            logger.warning(f"⚠️ Telegram FloodWait during startup: sleeping for {wait_time}s...")
            print(f"⚠️ Telegram FloodWait: Sleeping {wait_time}s before retrying client start...")
            await asyncio.sleep(wait_time + 1)
        except Exception as e:
            err_str = str(e)
            if "FLOOD_WAIT" in err_str.upper():
                match = re.search(r"FLOOD_WAIT_(\d+)", err_str.upper())
                wait_time = int(match.group(1)) if match else 60
                logger.warning(f"⚠️ Telegram FloodWait in exception ({e}): sleeping for {wait_time}s...")
                print(f"⚠️ Telegram FloodWait: Sleeping {wait_time}s before retrying...")
                await asyncio.sleep(wait_time + 1)
            else:
                logger.error(f"❌ Error during Telegram client start: {e}", exc_info=True)
                print(f"❌ Startup error: {e}. Retrying in 10s...")
                await asyncio.sleep(10)

    logger.info("🚀 CineVerse Bot is idle and listening for updates...")
    await idle()
    await app.stop()

def run_bot_safely():
    """
    Main runner that wraps app.run() inside a persistent retry loop
    catching any unhandled FloodWait or startup exceptions.
    """
    start_flask_server()
    print("=" * 60)
    print("🚀 CineVerse Movie Search Bot is starting...")
    print(f"📌 Admin ID: {ADMIN_ID} | DB Channel: {DB_CHANNEL_ID}")
    print("=" * 60)

    while True:
        try:
            app.run(_start_pyrogram_client())
            break
        except (KeyboardInterrupt, SystemExit):
            logger.info("Bot execution terminated by user or system signal.")
            break
        except FloodWait as e:
            wait_time = int(getattr(e, "value", getattr(e, "x", 60)))
            logger.warning(f"⚠️ Pyrogram FloodWait caught in runner: sleeping {wait_time}s...")
            print(f"⚠️ FloodWait: Sleeping {wait_time}s before reconnecting...")
            time.sleep(wait_time + 1)
        except Exception as e:
            err_str = str(e)
            if "FLOOD_WAIT" in err_str.upper():
                match = re.search(r"FLOOD_WAIT_(\d+)", err_str.upper())
                wait_time = int(match.group(1)) if match else 60
                logger.warning(f"⚠️ FloodWait in runner ({e}): sleeping {wait_time}s...")
                print(f"⚠️ FloodWait: Sleeping {wait_time}s before reconnecting...")
                time.sleep(wait_time + 1)
            else:
                logger.error(f"❌ Fatal error in bot runner: {e}", exc_info=True)
                print(f"❌ Runner error: {e}. Retrying in 10s...")
                time.sleep(10)

if __name__ == "__main__":
    run_bot_safely()

# CineVerse Patch v2

