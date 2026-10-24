"""
Mingle2 Bot - Database & Storage Layer
SQLite database for tracking accounts, discovered profiles, incoming likes, sent likes, and criteria filters.
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import Dict, Any, List, Optional

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "mingle2_bot.db")

os.makedirs(DB_DIR, exist_ok=True)

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Account table (our own synced account)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS my_account (
        id INTEGER PRIMARY KEY,
        user_id TEXT UNIQUE,
        username TEXT,
        profile_url TEXT,
        avatar_url TEXT,
        age INTEGER,
        location TEXT,
        last_synced_at TEXT,
        is_logged_in INTEGER DEFAULT 0
    )
    """)
    
    # Target profiles discovered
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS target_profiles (
        user_id TEXT PRIMARY KEY,
        username TEXT,
        headline TEXT,
        age INTEGER,
        gender TEXT,
        location TEXT,
        city TEXT,
        region TEXT,
        height TEXT,
        body_type TEXT,
        ethnicity TEXT,
        marital_status TEXT,
        children TEXT,
        smokes TEXT,
        drinks TEXT,
        religion TEXT,
        photo_url TEXT,
        online_status TEXT,
        profile_url TEXT,
        raw_attributes TEXT,
        discovered_at TEXT
    )
    """)
    
    # Likes received from other users (Who Liked Me / Mutual Match)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS likes_received (
        user_id TEXT PRIMARY KEY,
        username TEXT,
        photo_url TEXT,
        age INTEGER,
        location TEXT,
        received_at TEXT,
        liked_back INTEGER DEFAULT 0,
        liked_back_at TEXT
    )
    """)
    
    # Confirmed mutual matches (both users liked each other)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS mutual_matches (
        user_id TEXT PRIMARY KEY,
        username TEXT,
        photo_url TEXT,
        age INTEGER,
        gender TEXT,
        location TEXT,
        matched_at TEXT,
        profile_url TEXT,
        discovered_at TEXT
    )
    """)
    
    # Likes sent by our bot
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS likes_sent (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT UNIQUE,
        username TEXT,
        source TEXT, -- 'like_back' or 'search_criteria'
        matched_criteria TEXT,
        sent_at TEXT,
        status TEXT DEFAULT 'success'
    )
    """)
    
    # Profile inspections / skips cache to avoid re-inspecting or re-liking same candidates
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS profile_inspections (
        user_id TEXT PRIMARY KEY,
        username TEXT,
        is_match INTEGER DEFAULT 0,
        mismatch_reason TEXT,
        inspected_at TEXT
    )
    """)
    
    # Bot Settings & Search Filters
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bot_config (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    
    # Activity logs
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        level TEXT,
        message TEXT,
        user_id TEXT
    )
    """)
    
    # Normalize legacy blurred fan rating IDs
    try:
        cursor.execute("""
        UPDATE likes_received
        SET user_id = 'blurred_fan_' || user_id
        WHERE (user_id LIKE '10%' OR user_id LIKE '11%')
          AND user_id NOT LIKE 'blurred_fan_%'
          AND username LIKE '%.%';
        """)
    except Exception:
        pass

    conn.commit()
    conn.close()
    
    # Set default configurations if empty
    default_config = {
        "min_age": "20",
        "max_age": "38",
        "target_ethnicities": json.dumps(["Black / African descent", "Any"]),
        "target_body_types": json.dumps(["Average", "Athletic", "Slender", "Curvy", "Any"]),
        "target_locations": "Uganda, Kampala, Central Region, London, New York",
        "require_photo": "true",
        "max_likes_per_session": "50",
        "min_delay_seconds": "3",
        "max_delay_seconds": "7",
        "auto_like_back": "true"
    }
    
    conn = get_connection()
    c = conn.cursor()
    for k, v in default_config.items():
        c.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()

# Activity Logging
def log_activity(message: str, level: str = "INFO", user_id: Optional[str] = None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [{level}] {f'[User {user_id}] ' if user_id else ''}{message}")
    try:
        conn = get_connection()
        conn.execute("INSERT INTO activity_logs (timestamp, level, message, user_id) VALUES (?, ?, ?, ?)",
                     (now, level, message, user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Log DB Error: {e}")

# Config Helpers
def get_config() -> Dict[str, Any]:
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM bot_config").fetchall()
    conn.close()
    result = {}
    for r in rows:
        key = r["key"]
        val = r["value"]
        if key in ["target_ethnicities", "target_body_types"]:
            try:
                result[key] = json.loads(val)
            except Exception:
                result[key] = []
        elif key in ["min_age", "max_age", "max_likes_per_session", "min_delay_seconds", "max_delay_seconds"]:
            try:
                result[key] = int(val)
            except Exception:
                result[key] = 0
        elif key in ["require_photo", "auto_like_back"]:
            result[key] = str(val).lower() in ["true", "1", "yes"]
        else:
            result[key] = val
    return result

def update_config(config_dict: Dict[str, Any]):
    conn = get_connection()
    for k, v in config_dict.items():
        if isinstance(v, (list, dict)):
            val_str = json.dumps(v)
        elif isinstance(v, bool):
            val_str = "true" if v else "false"
        else:
            val_str = str(v)
        conn.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (k, val_str))
    conn.commit()
    conn.close()

# Account Sync
def save_account_sync(user_id: str, username: str, profile_url: str = "", avatar_url: str = "", age: int = 0, location: str = ""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute("""
    INSERT INTO my_account (id, user_id, username, profile_url, avatar_url, age, location, last_synced_at, is_logged_in)
    VALUES (1, ?, ?, ?, ?, ?, ?, ?, 1)
    ON CONFLICT(id) DO UPDATE SET
        user_id=excluded.user_id,
        username=excluded.username,
        profile_url=excluded.profile_url,
        avatar_url=excluded.avatar_url,
        age=excluded.age,
        location=excluded.location,
        last_synced_at=excluded.last_synced_at,
        is_logged_in=1
    """, (user_id, username, profile_url, avatar_url, age, location, now))
    conn.commit()
    conn.close()
    log_activity(f"My Account synced: ID '{user_id}', Username '{username}'", level="SUCCESS", user_id=user_id)

def get_my_account() -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM my_account WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else None

# Discovered Profiles
def save_discovered_profile(profile_data: Dict[str, Any]):
    user_id = str(profile_data.get("user_id", "")).strip()
    if not user_id:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute("""
    INSERT INTO target_profiles (
        user_id, username, headline, age, gender, location, city, region,
        height, body_type, ethnicity, marital_status, children, smokes, drinks, religion,
        photo_url, online_status, profile_url, raw_attributes, discovered_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET
        username=excluded.username,
        headline=excluded.headline,
        age=excluded.age,
        location=excluded.location,
        height=excluded.height,
        body_type=excluded.body_type,
        ethnicity=excluded.ethnicity,
        photo_url=excluded.photo_url,
        online_status=excluded.online_status
    """, (
        user_id,
        profile_data.get("username", ""),
        profile_data.get("headline", ""),
        profile_data.get("age", 0),
        profile_data.get("gender", ""),
        profile_data.get("location", ""),
        profile_data.get("city", ""),
        profile_data.get("region", ""),
        profile_data.get("height", ""),
        profile_data.get("body_type", ""),
        profile_data.get("ethnicity", ""),
        profile_data.get("marital_status", ""),
        profile_data.get("children", ""),
        profile_data.get("smokes", ""),
        profile_data.get("drinks", ""),
        profile_data.get("religion", ""),
        profile_data.get("photo_url", ""),
        profile_data.get("online_status", ""),
        profile_data.get("profile_url", ""),
        json.dumps(profile_data.get("raw_attributes", {})),
        now
    ))
    conn.commit()
    conn.close()

# Likes Received (Who Liked Me)
def record_like_received(user_id: str, username: str, photo_url: str = "", age: int = 0, location: str = "", received_at_str: str = ""):
    now = received_at_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute("""
    INSERT INTO likes_received (user_id, username, photo_url, age, location, received_at, liked_back)
    VALUES (?, ?, ?, ?, ?, ?, 0)
    ON CONFLICT(user_id) DO UPDATE SET
        username=excluded.username,
        photo_url=excluded.photo_url,
        age=CASE WHEN excluded.age > 0 THEN excluded.age ELSE likes_received.age END,
        location=CASE WHEN excluded.location != '' THEN excluded.location ELSE likes_received.location END,
        received_at=excluded.received_at
    """, (user_id, username, photo_url, age, location, now))
    conn.commit()
    conn.close()

def set_incoming_badge(badge: str):
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('incoming_likes_badge', ?)", (str(badge),))
    conn.commit()
    conn.close()

def get_incoming_badge() -> str:
    conn = get_connection()
    row = conn.execute("SELECT value FROM bot_config WHERE key = 'incoming_likes_badge'").fetchone()
    conn.close()
    return str(row["value"]) if row and row["value"] else ""

def mark_liked_back(user_id: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute("UPDATE likes_received SET liked_back = 1, liked_back_at = ? WHERE user_id = ?", (now, user_id))
    conn.commit()
    conn.close()

# Likes Sent
def record_like_sent(user_id: str, username: str, source: str = "search_criteria", matched_criteria: Optional[Dict[str, Any]] = None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    criteria_json = json.dumps(matched_criteria or {})
    conn.execute("""
    INSERT INTO likes_sent (user_id, username, source, matched_criteria, sent_at, status)
    VALUES (?, ?, ?, ?, ?, 'success')
    ON CONFLICT(user_id) DO UPDATE SET
        sent_at=excluded.sent_at,
        source=excluded.source
    """, (user_id, username, source, criteria_json, now))
    conn.commit()
    conn.close()
    log_activity(f"Liked {username} (ID: {user_id}) via [{source}]", level="LIKE_SENT", user_id=user_id)

def has_already_liked(user_id: str) -> bool:
    uid = str(user_id).strip()
    if not uid:
        return False
    conn = get_connection()
    # Check likes_sent
    row1 = conn.execute("SELECT id FROM likes_sent WHERE user_id = ?", (uid,)).fetchone()
    if row1:
        conn.close()
        return True
    # Check confirmed mutual matches
    row2 = conn.execute("SELECT user_id FROM mutual_matches WHERE user_id = ?", (uid,)).fetchone()
    if row2:
        conn.close()
        return True
    # Check likes_received where liked_back = 1
    row3 = conn.execute("SELECT user_id FROM likes_received WHERE user_id = ? AND liked_back = 1", (uid,)).fetchone()
    conn.close()
    return row3 is not None

def record_profile_inspected(user_id: str, username: str = "", is_match: bool = False, mismatch_reason: str = ""):
    uid = str(user_id).strip()
    if not uid:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute("""
    INSERT INTO profile_inspections (user_id, username, is_match, mismatch_reason, inspected_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET
        username=CASE WHEN excluded.username != '' THEN excluded.username ELSE profile_inspections.username END,
        is_match=excluded.is_match,
        mismatch_reason=excluded.mismatch_reason,
        inspected_at=excluded.inspected_at
    """, (uid, username, 1 if is_match else 0, mismatch_reason, now))
    conn.commit()
    conn.close()

def has_already_inspected(user_id: str) -> bool:
    uid = str(user_id).strip()
    if not uid:
        return False
    conn = get_connection()
    row = conn.execute("SELECT user_id FROM profile_inspections WHERE user_id = ?", (uid,)).fetchone()
    conn.close()
    return row is not None

def get_pending_like_backs(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("""
        SELECT user_id, username, photo_url, age, location
        FROM likes_received
        WHERE (liked_back = 0 OR liked_back IS NULL)
          AND user_id NOT IN (SELECT user_id FROM likes_sent)
          AND user_id NOT IN (SELECT user_id FROM mutual_matches)
          AND user_id NOT LIKE 'blurred_%'
          AND user_id NOT LIKE 'fan_%'
          AND user_id NOT LIKE '10%'
          AND user_id NOT LIKE '11%'
          AND LENGTH(user_id) = 8
        ORDER BY received_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# Confirmed Mutual Matches
def record_mutual_match(user_id: str, username: str, photo_url: str = "", age: int = 0, gender: str = "", location: str = "", matched_at: str = "", profile_url: str = ""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute("""
    INSERT INTO mutual_matches (user_id, username, photo_url, age, gender, location, matched_at, profile_url, discovered_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET
        username=excluded.username,
        photo_url=CASE WHEN excluded.photo_url != '' THEN excluded.photo_url ELSE mutual_matches.photo_url END,
        age=CASE WHEN excluded.age > 0 THEN excluded.age ELSE mutual_matches.age END,
        gender=CASE WHEN excluded.gender != '' THEN excluded.gender ELSE mutual_matches.gender END,
        location=CASE WHEN excluded.location != '' THEN excluded.location ELSE mutual_matches.location END,
        matched_at=CASE WHEN excluded.matched_at != '' THEN excluded.matched_at ELSE mutual_matches.matched_at END,
        profile_url=excluded.profile_url
    """, (user_id, username, photo_url, age, gender, location, matched_at, profile_url, now))
    conn.commit()
    conn.close()

def get_mutual_matches(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM mutual_matches ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_stats() -> Dict[str, Any]:
    conn = get_connection()
    total_discovered = conn.execute("SELECT COUNT(*) FROM target_profiles").fetchone()[0]
    total_likes_sent = conn.execute("SELECT COUNT(*) FROM likes_sent").fetchone()[0]
    db_likes_received = conn.execute("SELECT COUNT(*) FROM likes_received").fetchone()[0]
    total_mutual_liked = conn.execute("SELECT COUNT(*) FROM likes_sent WHERE source = 'like_back'").fetchone()[0]
    total_confirmed_matches = conn.execute("SELECT COUNT(*) FROM mutual_matches").fetchone()[0]
    
    badge = ""
    row = conn.execute("SELECT value FROM bot_config WHERE key = 'incoming_likes_badge'").fetchone()
    if row and row["value"]:
        badge = str(row["value"])
    conn.close()
    
    return {
        "total_discovered": total_discovered,
        "total_likes_sent": total_likes_sent,
        "total_likes_received": badge if badge else db_likes_received,
        "total_mutual_liked": total_mutual_liked,
        "total_confirmed_matches": total_confirmed_matches,
        "incoming_badge": badge
    }

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at:", DB_PATH)
