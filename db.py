import aiosqlite
import asyncio
from dotenv import load_dotenv
import os

load_dotenv()

DB = os.getenv("DB_PATH")

async def init_db():
    """Initialize all tables in the database."""
    async with aiosqlite.connect(DB) as db:
        # --- Scrims table ---
        await db.execute("""
        CREATE TABLE IF NOT EXISTS scrims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            start_time_utc TEXT NOT NULL,
            end_time_utc TEXT NOT NULL,
            contact TEXT,
            note TEXT
        )
        """)

        # --- Users table for timezones ---
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            timezone TEXT
        )
        """)

        # --- Attendance table (scrim_id + user_id) ---
        await db.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            scrim_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY (scrim_id, user_id)
        )
        """)

        # --- Config table for persistent info ---
        await db.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        await db.commit()


# --- Helper functions ---
async def set_config(key: str, value: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO config(key, value) VALUES(?, ?)",
            (key, value)
        )
        await db.commit()


async def get_config(key: str):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT value FROM config WHERE key=?",
            (key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


# --- test DB creation ---
if __name__ == "__main__":
    asyncio.run(init_db())
    print("Database initialized.")