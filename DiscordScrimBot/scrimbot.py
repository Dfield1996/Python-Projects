import discord
from dotenv import load_dotenv
import os
import aiosqlite
import pytz
import calendar
from db import init_db, get_config, set_config
from datetime import datetime
from discord.ext import tasks

load_dotenv()

DB = os.getenv("DB_PATH")
TEST_GUILD_ID = int(os.getenv("TEST_GUILD_ID"))
SCRIMS_CHANNEL_ID = int(os.getenv("SCRIMS_CHANNEL_ID"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Bot(intents=intents)

# --- Get or create the permanent scrims board message ---
async def get_or_create_scrims_board(channel):
    scrims_message_id = await get_config("scrims_message_id")
    scrims_msg = None

    if scrims_message_id:
        try:
            scrims_msg = await channel.fetch_message(int(scrims_message_id))
        except:
            scrims_msg = None

    if not scrims_msg:
        embed = discord.Embed(
            title="Upcoming Scrims",
            description="No scrims yet",
            color=discord.Color.green()
        )
        scrims_msg = await channel.send(embed=embed)
        await set_config("scrims_message_id", str(scrims_msg.id))

    return scrims_msg

# --- Updates the embed with scrim data ---
async def update_scrims_embed(msg):
    now = datetime.now(pytz.utc)
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            """SELECT name, start_time_utc, end_time_utc, contact, note 
               FROM scrims 
               WHERE end_time_utc > ? 
               ORDER BY start_time_utc ASC""",
            (now.isoformat(),)
        )
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
            embed.add_field(name="\u200b", value="\u200b", inline=False)
        first_date = False

        weekday = calendar.day_name[date_key.weekday()]
        embed.add_field(
            name=f"📅 **{weekday.upper()} {date_key.strftime('%d/%m')}**",
            value="━━━━━━━━━━━━━━━━",
            inline=False
        )

        for name, start_dt, end, contact, note in scrims_by_date[date_key]:
            end_dt = datetime.fromisoformat(end).replace(tzinfo=pytz.utc)
            start_ts = int(start_dt.timestamp())
            end_ts = int(end_dt.timestamp())
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

# --- Exposed function for main.py to call ---
async def refresh_scrims():
    """Fetch scrims board message and update embed immediately."""
    channel = bot.get_channel(SCRIMS_CHANNEL_ID)
    if channel:
        scrims_msg = await get_or_create_scrims_board(channel)
        await update_scrims_embed(scrims_msg)

# ------------------------ BOT EVENT ------------------------ #

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    channel = bot.get_channel(SCRIMS_CHANNEL_ID)
    await bot.sync_commands()

    scrims_msg = await get_or_create_scrims_board(channel)
    await update_scrims_embed(scrims_msg)

    if not reminder_loop.is_running():
        reminder_loop.start()

    try:
        from main import set_bot_instance
        set_bot_instance(bot)
    except:
        pass

# ------------------------ TASKS ------------------------ #

sent_reminders = {}

@tasks.loop(minutes=1)
async def reminder_loop():
    now = datetime.now(pytz.utc)

    async with aiosqlite.connect(DB) as db:
        # Delete scrims that have ended
        await db.execute("DELETE FROM scrims WHERE end_time_utc <= ?", (now.isoformat(),))
        await db.commit()

        # Get remaining scrims for reminders
        cursor = await db.execute("SELECT name, start_time_utc FROM scrims ORDER BY start_time_utc ASC")
        scrims = await cursor.fetchall()

        channel = bot.get_channel(SCRIMS_CHANNEL_ID)
        if not channel:
            print("Channel not found!")
            return
        
    scrims_msg = await get_or_create_scrims_board(channel)
    await update_scrims_embed(scrims_msg)

    for title, start in scrims:
        start_dt = datetime.fromisoformat(start).replace(tzinfo=pytz.utc)
        diff = (start_dt - now).total_seconds() / 60

        print(f"Checking scrim '{title}': {diff:.1f} minutes away")

        # Delete all reminders when scrim starts (diff <= 0)
        if diff <= 0:
            print(f"Scrim '{title}' has started, deleting all reminders...")
            keys_to_delete = [key for key in sent_reminders.keys() if key[0] == title]
            for old_key in keys_to_delete:
                try:
                    await sent_reminders[old_key].delete()
                    print(f"Deleted reminder: {old_key}")
                except Exception as e:
                    print(f"Failed to delete reminder {old_key}: {e}")
                del sent_reminders[old_key]
            continue

        # Send reminders at 30, 15, and 5 minutes
        for reminder_time in [30, 15, 5]:
            key = (title, reminder_time)
            
            # Check if we're within 0.5 minutes of the reminder time
            if abs(diff - reminder_time) < 0.5 and key not in sent_reminders:
                print(f"Sending {reminder_time}min reminder for '{title}'")
                msg = await channel.send(f"@everyone **{title}** starting in {int(round(diff))} minutes")
                sent_reminders[key] = msg

                # Delete previous reminders for this scrim (e.g., when 15min arrives, delete 30min)
                keys_to_delete = [
                    old_key for old_key in sent_reminders.keys() 
                    if old_key[0] == title and old_key[1] > reminder_time
                ]
                
                for old_key in keys_to_delete:
                    try:
                        await sent_reminders[old_key].delete()
                        print(f"Deleted old reminder: {old_key}")
                    except Exception as e:
                        print(f"Failed to delete old reminder {old_key}: {e}")
                    del sent_reminders[old_key]
            continue

        for reminder in [30, 15, 5]:
            key = (title, reminder)
            if abs(diff - reminder) < 0.5 and key not in sent_reminders:
                msg = await channel.send(f"@everyone **{title}** starting in {int(round(diff))} minutes")
                sent_reminders[key] = msg

                for old_key in list(sent_reminders.keys()):
                    if old_key[0] == title and old_key != key:
                        try:
                            await sent_reminders[old_key].delete()
                        except:
                            pass
                        del sent_reminders[old_key]

async def start_bot():
    await bot.start(os.getenv("DISCORD_TOKEN"))