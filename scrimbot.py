import discord
from dotenv import load_dotenv
import os
import aiosqlite
import pytz
import calendar
from db import DB, init_db, get_config, set_config
from datetime import datetime
from discord.ext import tasks
from discord.ui import View, Button, Select, Modal, InputText
from discord import Option

load_dotenv()


DB = os.getenv("DB_PATH")
TEST_GUILD_ID = int(os.getenv("TEST_GUILD_ID"))
SCRIMS_CHANNEL_ID = int(os.getenv("SCRIMS_CHANNEL_ID"))


# --- Modal for editing a scrim entry
class ScrimEditModal(Modal):
    def __init__(self, scrim_id, current_name, current_start, current_end, current_contact, current_note):
        super().__init__(title="Edit Scrim")
        self.scrim_id = scrim_id

        self.add_item(InputText(label="Name", value=current_name))
        self.add_item(InputText(label="Start Time (DD/MM/YYYY HH:MMAM/PM)", value=current_start))
        self.add_item(InputText(label="End Time (DD/MM/YYYY HH:MMAM/PM)", value=current_end))
        self.add_item(InputText(label="Contact", value=current_contact or "", required=False))
        self.add_item(InputText(label="Note", value=current_note or "", required=False))

    async def callback(self, interaction):
        name = self.children[0].value
        start_str = self.children[1].value
        end_str = self.children[2].value
        contact = self.children[3].value
        note = self.children[4].value

        # Parse to UTC
        try:
            local_tz = pytz.timezone("Australia/Melbourne")  # adjust as needed
            start_dt = datetime.strptime(start_str, "%d/%m/%Y %I:%M%p")
            end_dt = datetime.strptime(end_str, "%d/%m/%Y %I:%M%p")
            start_utc = local_tz.localize(start_dt).astimezone(pytz.UTC).isoformat()
            end_utc = local_tz.localize(end_dt).astimezone(pytz.UTC).isoformat()
        except Exception:
            await interaction.response.send_message("Invalid date/time format", ephemeral=True)
            return

        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "UPDATE scrims SET name=?, start_time_utc=?, end_time_utc=?, contact=?, note=? WHERE rowid=?",
                (name, start_utc, end_utc, contact, note, self.scrim_id)
            )
            await db.commit()

        channel = bot.get_channel(SCRIMS_CHANNEL_ID)
        msg = await get_or_create_scrims_board(channel)
        await update_scrims_embed(msg)

        await interaction.response.send_message("Scrim updated ✅", ephemeral=True)

async def get_or_create_scrims_board(channel):
    scrims_message_id = await get_config("scrims_message_id")
    scrims_msg = None

    # Try to fetch existing message
    if scrims_message_id:
        try:
            scrims_msg = await channel.fetch_message(int(scrims_message_id))
        except:
            scrims_msg = None

    # If no message exists, create it and attach the persistent view
    if not scrims_msg:
        embed = discord.Embed(
            title="Upcoming Scrims",
            description="No scrims yet",
            color=discord.Color.green()
        )
        scrims_msg = await channel.send(embed=embed)  # attach view
        await set_config("scrims_message_id", str(scrims_msg.id))

    return scrims_msg

# --- Updates the embed with the data entered from scrim_add
async def update_scrims_embed(msg):
    now = datetime.now(pytz.utc)
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute("""SELECT name, start_time_utc, end_time_utc, contact, note FROM scrims WHERE end_time_utc > ? ORDER BY start_time_utc ASC""",(now.isoformat(),))
        scrims = await cursor.fetchall()

    embed = discord.Embed(title="⚔️  SCRIM SCHEDULE  ⚔️", color=discord.Color.green())
    embed.description = "\u200b"

    if not scrims:
        embed.description = "No scrims scheduled"
        await msg.edit(embed=embed)
        return
    
    scrims_by_date = {}
    for name, start, end, contact, note in scrims:
        start_dt = datetime.fromisoformat(start).replace(tzinfo=pytz.utc)
        date_key = start_dt.date()
        if date_key not in scrims_by_date:
            scrims_by_date[date_key] = []
        scrims_by_date[date_key].append((name, start_dt, end, contact, note))

    first_date = True

    for date_key in sorted(scrims_by_date.keys()):
        if not first_date:
            embed.add_field(
            name="\u200b",
            value="\u200b",
            inline=False
        )
            
        first_date = False

        weekday = calendar.day_name[date_key.weekday()]
        embed.add_field(
            name=f"📅 **{weekday.upper()} {date_key.strftime('%d/%m')}**",
            value="━━━━━━━━━━━━━━━━",
            inline=False
        )
            
        for name, start_dt, end, contact, note in scrims_by_date[date_key]:
            start_ts = int(start_dt.timestamp())
            end_ts = int(datetime.fromisoformat(end).replace(tzinfo=pytz.utc).timestamp())

            value_lines = [f"🕒 <t:{start_ts}:t> → <t:{end_ts}:t>"]
            if contact:
                value_lines.append(f"👤 **Contact:** {contact}")
            if note:
                value_lines.append(f"📝 **Note:** {note}")

            embed.add_field(
            name=f"**{name.upper()}**",
            value="\n".join(value_lines),
            inline=False
            )

    await msg.edit(embed=embed, view=None)

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Bot(intents=intents)

# ------------------------ BOT EVENT ------------------------ #

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    channel = bot.get_channel(SCRIMS_CHANNEL_ID) # pulls channel id from scrims_channel_id
    await bot.sync_commands() # forces Discord to register the slash commands
   
    scrims_msg = await get_or_create_scrims_board(channel)

    # Starts reminder loop if it isn't running which deletes old reminders and all reminders once scrim has begun
    if not reminder_loop.is_running():
        reminder_loop.start()

# ------------------------ TASKS ------------------------ #

sent_reminders = {}

# keeps channel clean of reminders and reminders that have reached the scrims start time
@tasks.loop(minutes=1)
async def reminder_loop():
    now = datetime.now(pytz.utc)

    async with aiosqlite.connect(DB) as db:

        # --- Deletes scrims from the list that have passed their end time ---
        await db.execute("DELETE FROM scrims WHERE end_time_utc <= ?", (now.isoformat(),))
        await db.commit()

        # Gets remaining scrims for reminders
        cursor = await db.execute("SELECT name, start_time_utc FROM scrims ORDER BY start_time_utc ASC")
        scrims = await cursor.fetchall()

        channel = bot.get_channel(SCRIMS_CHANNEL_ID)
        if not channel:
            return
        
    scrims_msg = await get_or_create_scrims_board(channel)
    await update_scrims_embed(scrims_msg)

    for title, start in scrims:
        start_dt = datetime.fromisoformat(start).replace(tzinfo=pytz.utc)
        diff = (start_dt - now).total_seconds() / 60

        if diff <= 0:
            for old_key in list(sent_reminders.keys()):
                if old_key[0] == title:
                    try:
                        await sent_reminders[old_key].delete()
                    except:
                        pass
                    del sent_reminders[old_key]
                    continue

        for reminder in [30, 15, 5]:
            key = (title, reminder)
            if abs(diff - reminder) < 0.5 and key not in sent_reminders:
                msg = await channel.send(f"@everyone **{title}** starting in {int(round(diff))} minutes")
                sent_reminders[key] = msg  # mark as sent

                for old_key in list(sent_reminders.keys()):
                    if old_key[0] == title and old_key != key:
                        try:
                            await sent_reminders[old_key].delete()
                        except:
                            pass
                        del sent_reminders[old_key]

# ------------------------ SLASH COMMANDS ------------------------ #

# --- Command for adding a scrim to the embed list ---
@bot.slash_command( 
    description="Add a new scrim",
    guild_ids=[TEST_GUILD_ID]
)
async def scrim_add(
    ctx,
    name: str = Option(description="Name of the Team/VOD"),
    date: str = Option(description="Date of scrim in DD/MM/YYYY"),
    start_time: str = Option(description="Start time e.g., 8:00PM"),
    end_time: str = Option(description="End time e.g., 10:00PM"),
    contact: str = Option(description="Optional contact name", required=False, default=None),
    note: str = Option(description="Optional note", required=False, default=None)
):

    try:
        # Parse date and times
        start_dt = datetime.strptime(f"{date} {start_time}", "%d/%m/%Y %I:%M%p")
        end_dt = datetime.strptime(f"{date} {end_time}", "%d/%m/%Y %I:%M%p")
    except ValueError:
        await ctx.respond(
            "Invalid date or time format. Use DD/MM/YYYY and HH:MMAM/PM (e.g., 23/12/2025 8:00PM)",
            ephemeral=True
        )
        return
    
    # Convert to UTC for DB storage
    local_tz = pytz.timezone("Australia/Melbourne")  # adjust as needed
    start_utc = local_tz.localize(start_dt).astimezone(pytz.UTC)
    end_utc = local_tz.localize(end_dt).astimezone(pytz.UTC)

    # Save scrim to DB
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """INSERT INTO scrims(name, start_time_utc, end_time_utc, contact, note) VALUES (?, ?, ?, ?, ?)""",
            (name, start_utc.isoformat(), end_utc.isoformat(), contact, note)
        )
        await db.commit()

    # Fetch the permanent scrims message
    channel = bot.get_channel(SCRIMS_CHANNEL_ID)
    scrims_msg = await get_or_create_scrims_board(channel)

    # Update the embed with scrims and local times for users
    await update_scrims_embed(scrims_msg)

    await ctx.respond("Scrim added ✅", delete_after=5)

# --- lists all the scrims that are upcoming/ongoing, with their row ID, team name and start date/time, for editing purposes ---
@bot.slash_command(description="List all upcoming scrims", guild_ids=[TEST_GUILD_ID])
async def list_scrims(ctx):
    now = datetime.now(pytz.utc)
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT rowid, name, start_time_utc FROM scrims WHERE end_time_utc > ? ORDER BY start_time_utc ASC",
            (now.isoformat(),)
        )
        scrims = await cursor.fetchall()

    if not scrims:
        await ctx.respond("No upcoming scrims.", ephemeral=True)
        return

    lines = []
    for rowid, name, start in scrims:
        start_dt = datetime.fromisoformat(start).replace(tzinfo=pytz.utc)
        lines.append(f"ID: `{rowid}` | **{name}** | <t:{int(start_dt.timestamp())}:f>")

    await ctx.respond("\n".join(lines), ephemeral=True)

# --- Used to make changes to a scrim already listed on the schedule
@bot.slash_command(description="Edit a scrim", guild_ids=[TEST_GUILD_ID])
async def edit_scrim(ctx, scrim_id: int):
    """Provide the rowid of the scrim to edit."""
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT name, start_time_utc, end_time_utc, contact, note FROM scrims WHERE rowid=?",
            (scrim_id,)
        )
        scrim = await cursor.fetchone()

    if not scrim:
        await ctx.respond("Scrim not found", ephemeral=True)
        return

    modal = ScrimEditModal(scrim_id, scrim[0], scrim[1], scrim[2], scrim[3], scrim[4])
    await ctx.send_modal(modal)

bot.run(os.getenv("DISCORD_TOKEN"))