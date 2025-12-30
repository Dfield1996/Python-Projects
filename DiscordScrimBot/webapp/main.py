from fastapi import FastAPI, Request, HTTPException, Body, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import pytz
from datetime import datetime, timedelta, time
from collections import defaultdict
import os
import asyncio
import aiosqlite
from typing import Dict
from scrimbot import start_bot

from db import init_db, get_players, update_player_name, set_availability, get_availability

from scrimbot import refresh_scrims  # <- Import the function

DB = os.getenv("DB_PATH", "scrims.db")
app = FastAPI()

# Current player list (names only)
player_names = ["Dfield", "Slidzorj", "Infima", "Chappadoodle", "Player 5", "Player 6"]

# --- INTERNAL FUNCTION FOR PLAYER DATA ---
def fetch_players():
    """Return list of players with explicit IDs"""
    return [{"id": i+1, "name": name} for i, name in enumerate(player_names)]

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

local_tz = pytz.timezone("Australia/Melbourne")
DAYS = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

@app.on_event("startup")
async def startup():
    await init_db()
    # Start Discord bot in background
    asyncio.create_task(start_bot())

@app.get("/")
@app.get("/scrims")
async def scrims(request: Request):
    scrims_by_day = await get_scrims_grouped()
    return templates.TemplateResponse(
        "scrims.html",
        {"request": request, "scrims_by_day": scrims_by_day}
    )

async def get_scrims_grouped():
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute("""
            SELECT rowid, name, start_time_utc, end_time_utc, contact, note
            FROM scrims
            ORDER BY start_time_utc ASC
        """)
        rows = await cursor.fetchall()

    grouped = defaultdict(list)

    for row in rows:
        start_dt = datetime.fromisoformat(row[2]).astimezone(local_tz)
        end_dt = datetime.fromisoformat(row[3]).astimezone(local_tz)

        day_key = start_dt.strftime("%A %d/%m/%Y")
        grouped[day_key].append({
            "id": row[0],
            "name": row[1],
            "time_display": f"{start_dt.strftime('%I:%M %p')} → {end_dt.strftime('%I:%M %p')}",
            "date_iso": start_dt.strftime("%Y-%m-%d"),
            "start_iso": start_dt.strftime("%H:%M"),
            "end_iso": end_dt.strftime("%H:%M"),
            "contact": row[4],
            "note": row[5]
        })

    return grouped

# --- ADD SCRIM ---
@app.post("/add")
async def add_scrim(data: dict = Body(...)):
    name = data.get("name")
    date = data.get("date")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    contact = data.get("contact")
    note = data.get("note")

    if not all([name, date, start_time, end_time]):
        raise HTTPException(status_code=400, detail="Missing required fields")

    start_dt_local = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
    end_dt_local = datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %H:%M")
    start_dt_utc = start_dt_local.astimezone(pytz.utc).isoformat()
    end_dt_utc = end_dt_local.astimezone(pytz.utc).isoformat()

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO scrims (name, start_time_utc, end_time_utc, contact, note) VALUES (?, ?, ?, ?, ?)",
            (name, start_dt_utc, end_dt_utc, contact, note)
        )
        await db.commit()

    await refresh_scrims()  # Immediately update Discord
    return {"success": True}

# --- DELETE SCRIM ---
@app.post("/delete")
async def delete_scrim(data: dict = Body(...)):
    scrim_id = data.get("scrim_id")
    if not scrim_id:
        raise HTTPException(status_code=400, detail="scrim_id is required")

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute("DELETE FROM scrims WHERE rowid = ?", (scrim_id,))
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Scrim not found")

    await refresh_scrims()
    return {"success": True}

# --- EDIT SCRIM ---
@app.post("/edit")
async def edit_scrim(data: dict = Body(...)):
    scrim_id = data.get("scrim_id")
    name = data.get("name")
    date = data.get("date")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    contact = data.get("contact")
    note = data.get("note")

    if not all([scrim_id, name, date, start_time, end_time]):
        raise HTTPException(status_code=400, detail="Missing required fields")

    start_dt_local = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
    end_dt_local = datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %H:%M")
    start_dt_utc = start_dt_local.astimezone(pytz.utc).isoformat()
    end_dt_utc = end_dt_local.astimezone(pytz.utc).isoformat()

    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            """UPDATE scrims
               SET name = ?, start_time_utc = ?, end_time_utc = ?, contact = ?, note = ?
               WHERE rowid = ?""",
            (name, start_dt_utc, end_dt_utc, contact, note, scrim_id)
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Scrim not found")

    await refresh_scrims()
    return {"success": True}

# --- AVAILABILITY PAGE ---
@app.get("/availability")
async def availability_page(request: Request):
    return templates.TemplateResponse("availability.html", {"request": request})

@app.get("/api/availability")
async def get_availability(player_id: int) -> Dict:
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT day, time, status FROM availability WHERE player_id = ?", (player_id,)
        )
        rows = await cursor.fetchall()

    result = defaultdict(dict)
    for day, time, status in rows:
        result[day][time] = status
    return dict(result)


# --- POST availability ---
@app.post("/api/availability")
async def set_availability_api(data: dict = Body(...)):
    player_id = int(data["player_id"])
    day_short = data.get("day")   # "Monday", etc.
    time = data.get("time")
    status = data.get("status")
    week_offset = int(data.get("week_offset", 0))  # Pass from frontend

    if not all([day_short, time, status]):
        raise HTTPException(400, "Missing required fields")

    # Compute full date string for the day
    today = datetime.now(local_tz)
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    day_index = DAYS.index(day_short)
    full_date = (monday + timedelta(days=day_index)).strftime("%A %d/%m/%Y")

    async with aiosqlite.connect(DB) as db:
        if status == "none":
            await db.execute(
                "DELETE FROM availability WHERE player_id = ? AND day = ? AND time = ?",
                (player_id, full_date, time)
            )
        else:
            # Upsert: delete old value first
            await db.execute(
                "DELETE FROM availability WHERE player_id = ? AND day = ? AND time = ?",
                (player_id, full_date, time)
            )
            await db.execute(
                "INSERT INTO availability (player_id, day, time, status) VALUES (?, ?, ?, ?)",
                (player_id, full_date, time, status)
            )
        await db.commit()

    return JSONResponse({"success": True})

@app.get("/api/availability_all")
async def get_all_availability(date: str = Query(...)):
    dt = datetime.strptime(date, "%Y-%m-%d")
    day_key = dt.strftime("%A %d/%m/%Y")

    result = {}
    players = fetch_players()  # use the internal helper instead of await get_players()

    for player in players:
        pid = player["id"]
        availability = await get_availability(pid)  # still async for DB
        result[pid] = availability

    return result

# --- Updates players named that has been edited
@app.post("/api/player")
async def edit_player(data: dict = Body(...)):
    player_id = data.get("id")
    name = data.get("name")
    if player_id is None or not name:
        raise HTTPException(status_code=400, detail="Invalid request")
    await update_player_name(int(player_id), name)
    return {"success": True}

# --- PLAYERS API GET ---
@app.get("/api/players")
async def get_players_endpoint():
    """API endpoint to return player list from DB"""
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute("SELECT id, name FROM players ORDER BY id ASC")
        rows = await cursor.fetchall()
    players = [{"id": row[0], "name": row[1]} for row in rows]
    return JSONResponse(players)

# --- PLAYERS API POST ---
@app.post("/api/players")
async def set_players_api(data: dict = Body(...)):
    players = data["players"]
    if len(players) != 6:
        raise HTTPException(status_code=400, detail="Must provide exactly 6 players")
    for i, name in enumerate(players, start=1):
        await update_player_name(i, name)
    return JSONResponse({"success": True})

@app.get("/health")
async def health_check():
    return {"status": "ok"}