"""Key-value store service."""
import json
import os
import sqlite3
from typing import Any, Dict, Optional

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
        print(
            f"SQLite database already contains {existing_count} entries. Skipping migration."
        )
        return

    print(f"Migrating data from {OLD_DB_FILE} to {DB_FILE}...")

    try:
        # Load data from old JSON file
        with open(OLD_DB_FILE, "r", encoding="utf-8") as f:
            old_data = json.load(f)

        if not old_data:
            print("No data found in old JSON file.")
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

        print(
            f"Successfully migrated {migrated_count} key-value pairs to SQLite."
        )

        # Backup the old file by renaming it
        backup_file = f"{OLD_DB_FILE}.backup"
        if os.path.exists(backup_file):
            os.remove(backup_file)
        os.rename(OLD_DB_FILE, backup_file)
        print(f"Old JSON file backed up to {backup_file}")

    except Exception as e:
        print(f"Error during migration: {str(e)}")
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
        return {"k": key, "v": value}
    return None
