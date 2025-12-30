import aiosqlite
import asyncio
import os
from typing import Dict
from datetime import datetime
from pathlib import Path

DB = os.getenv("DB_PATH", "scrims.db")

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

        # --- Users table ---
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            timezone TEXT
        )
        """)

        # --- Attendance table ---
        await db.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            scrim_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY (scrim_id, user_id)
        )
        """)

        # --- Config table ---
        await db.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        # --- Players table ---
        await db.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY,       -- 1 to 6
            name TEXT NOT NULL
        )
        """)

        # Seed default 6 players if table empty
        cursor = await db.execute("SELECT COUNT(*) FROM players")
        count = (await cursor.fetchone())[0]
        if count == 0:
            for i in range(1, 7):
                await db.execute("""
                INSERT INTO players (id, name) VALUES (?, ?)
                """, (i, f"Player {i}"))

        # --- Availability table ---
        await db.execute("""
        CREATE TABLE IF NOT EXISTS availability (
            player_id INTEGER,
            day TEXT,
            time TEXT,
            status TEXT,
            PRIMARY KEY (player_id, day, time),
            FOREIGN KEY (player_id) REFERENCES players(id)
        )
        """)

        await db.commit()


# --- Config helpers ---
async def set_config(key: str, value: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO config(key, value) VALUES(?, ?)",
            (key, value)
        )
        await db.commit()

async def get_config(key: str):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute("SELECT value FROM config WHERE key=?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else None


# --- Player helpers ---
async def get_players():
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute("SELECT id, name FROM players ORDER BY id ASC")
        rows = await cursor.fetchall()
        return [{"id": r[0], "name": r[1]} for r in rows]

async def update_player_name(player_id: int, name: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE players SET name=? WHERE id=?", (name, player_id))
        await db.commit()


# --- Availability helpers ---

async def get_all_availability(date: str) -> Dict[int, Dict[str, Dict[str, str]]]:
    """
    Returns a dict like:
    { player_id: { "Monday 30/12/2025": { "18:00": "available", ... } } }
    """
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute("SELECT id, name FROM players")
        players = await cursor.fetchall()

        result = {}
        for player_id, _ in players:
            cursor2 = await db.execute(
                "SELECT time, status FROM availability WHERE player_id=? AND day=?",
                (player_id, date)
            )
            slots = await cursor2.fetchall()
            day_dict = {time: status for time, status in slots}
            # Convert YYYY-MM-DD to "Monday DD/MM/YYYY"
            day_str = datetime.strptime(date, "%Y-%m-%d").strftime("%A %d/%m/%Y")
            result[player_id] = {day_str: day_dict}

        return result
    
async def set_availability(player_id: int, day: str, time: str, status: str):
    async with aiosqlite.connect(DB) as db:
        if status == "none":
            await db.execute(
                "DELETE FROM availability WHERE player_id=? AND day=? AND time=?",
                (player_id, day, time)
            )
        else:
            await db.execute("""
                INSERT INTO availability (player_id, day, time, status)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(player_id, day, time) DO UPDATE SET status=excluded.status
            """, (player_id, day, time, status))
        await db.commit()

async def get_availability(player_id: int):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT day, time, status FROM availability WHERE player_id=?",
            (player_id,)
        )
        rows = await cursor.fetchall()
        schedule = {}
        for day, time, status in rows:
            if day not in schedule:
                schedule[day] = {}
            schedule[day][time] = status
        return schedule


# --- Test DB creation ---
if __name__ == "__main__":
    asyncio.run(init_db())
    print(f"Database initialized at {DB}")
