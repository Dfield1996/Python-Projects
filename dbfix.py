import aiosqlite
import asyncio

DB = "scrims.db"

async def init_db():
    async with aiosqlite.connect(DB) as db:
        # Drop old table if testing
        await db.execute("DROP TABLE IF EXISTS scrims")
        await db.execute("DROP TABLE IF EXISTS users")
        await db.execute("DROP TABLE IF EXISTS attendance")

        # Create new tables
        await db.execute("""
        CREATE TABLE IF NOT EXISTS scrims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            start_time_utc TEXT NOT NULL,
            end_time_utc TEXT NOT NULL
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            timezone TEXT
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            scrim_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY (scrim_id, user_id)
        )
        """)
        await db.commit()

asyncio.run(init_db())