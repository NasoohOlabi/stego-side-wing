"""Key-value store service."""
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DB_FILE = "kv_store.db"
OLD_DB_FILE = "kv_store.json"


def init_db() -> None:
    """Initialize the SQLite database and create the kv table if it doesn't exist."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.commit()
    conn.close()


def migrate_json_to_sqlite() -> None:
    """Migrate data from the old JSON file to SQLite database."""
    if not os.path.exists(OLD_DB_FILE):
        return  # No old file to migrate

    # Initialize database first
    init_db()

    # Check if SQLite database already has data
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM kv")
    existing_count = cursor.fetchone()[0]
    conn.close()

    if existing_count > 0:
        logger.info(
            "kv_migrate_skip",
            extra={
                "event": "kv",
                "action": "migrate",
                "reason": "db_nonempty",
                "existing_count": existing_count,
            },
        )
        return

    logger.info(
        "kv_migrate_start",
        extra={"event": "kv", "action": "migrate", "from": OLD_DB_FILE, "to": DB_FILE},
    )

    try:
        # Load data from old JSON file
        with open(OLD_DB_FILE, "r", encoding="utf-8") as f:
            old_data = json.load(f)

        if not old_data:
            logger.info(
                "kv_migrate_empty",
                extra={"event": "kv", "action": "migrate", "reason": "empty_json"},
            )
            return

        # Insert all key-value pairs into SQLite
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        migrated_count = 0
        for key, value in old_data.items():
            # Serialize value to JSON string for storage
            value_json = json.dumps(value)
            cursor.execute(
                "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
                (key, value_json),
            )
            migrated_count += 1

        conn.commit()
        conn.close()

        logger.info(
            "kv_migrate_done",
            extra={"event": "kv", "action": "migrate", "migrated_count": migrated_count},
        )

        # Backup the old file by renaming it
        backup_file = f"{OLD_DB_FILE}.backup"
        if os.path.exists(backup_file):
            os.remove(backup_file)
        os.rename(OLD_DB_FILE, backup_file)
        logger.info(
            "kv_migrate_backup",
            extra={"event": "kv", "action": "migrate", "backup": backup_file},
        )

    except Exception as e:
        logger.exception(
            "kv_migrate_failed",
            extra={"event": "kv", "action": "migrate", "error": str(e)},
        )
        raise


def set_value(key: str, value: Any) -> Dict[str, Any]:
    """
    Set a key-value pair in the store.
    
    Args:
        key: Key name
        value: Value to store (will be JSON serialized)
        
    Returns:
        Dict with status and data
    """
    # Serialize value to JSON string for storage
    value_json = json.dumps(value)

    # Insert or replace the key-value pair in SQLite
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value_json)
    )
    conn.commit()
    conn.close()

    logger.info(
        "kv_set",
        extra={"event": "kv", "action": "set", "key": key},
    )
    return {
        "status": "success",
        "message": f'Key "{key}" saved.',
        "data": {key: value},
    }


def get_value(key: str) -> Optional[Dict[str, Any]]:
    """
    Get a value by key.
    
    Args:
        key: Key name
        
    Returns:
        Dict with 'k' and 'v' keys, or None if not found
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM kv WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()

    if row:
        # Deserialize the JSON value
        value = json.loads(row[0])
        logger.info(
            "kv_get",
            extra={"event": "kv", "action": "get", "key": key, "found": True},
        )
        return {"k": key, "v": value}
    logger.info(
        "kv_get",
        extra={"event": "kv", "action": "get", "key": key, "found": False},
    )
    return None


def list_values(limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """
    List key-value entries with basic pagination.

    Args:
        limit: Maximum number of entries to return
        offset: Number of entries to skip

    Returns:
        Dict containing entries and pagination metadata
    """
    safe_limit = max(1, min(int(limit), 1000))
    safe_offset = max(0, int(offset))

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM kv")
    total = int(cursor.fetchone()[0])
    cursor.execute(
        "SELECT key, value FROM kv ORDER BY key ASC LIMIT ? OFFSET ?",
        (safe_limit, safe_offset),
    )
    rows = cursor.fetchall()
    conn.close()

    items: List[Dict[str, Any]] = []
    for key, raw_value in rows:
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            parsed = raw_value
        items.append({"k": key, "v": parsed})

    return {
        "items": items,
        "pagination": {
            "limit": safe_limit,
            "offset": safe_offset,
            "returned": len(items),
            "total": total,
        },
    }


def delete_value(key: str) -> Dict[str, Any]:
    """
    Delete an entry by key.

    Args:
        key: Key name

    Returns:
        Dict with deletion result and existence flag
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM kv WHERE key = ?", (key,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()

    logger.info(
        "kv_delete",
        extra={"event": "kv", "action": "delete", "key": key, "deleted": deleted},
    )
    return {
        "status": "success",
        "deleted": deleted,
        "key": key,
    }
