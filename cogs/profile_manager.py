import discord
import asyncio
import logging
from discord.ext import commands


class ProfileManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log = logging.getLogger(self.__class__.__name__)  # Get a logger for this cog

    @commands.Cog.listener()
    async def on_ready(self):
        """This runs once the cog is loaded and the bot is ready."""
        self.log.info("Cog is ready.")

        # Set initial status from config file immediately on startup
        status_config = self.bot.config.get("initial_status", {})
        status_type = status_config.get("type", "playing")
        status_text = status_config.get("text", "with the fabric of reality")
        await self.set_status(status_type, status_text)

    async def set_status(self, status_type: str, name: str):
        """Sets the bot's activity status (e.g., 'playing', 'watching', 'custom')."""
        status_type = status_type.lower()
        activity: discord.BaseActivity | None = None

        if status_type == 'playing':
            activity = discord.Game(name=name)
        elif status_type == 'watching':
            activity = discord.Activity(type=discord.ActivityType.watching, name=name)
        elif status_type == 'listening':
            activity = discord.Activity(type=discord.ActivityType.listening, name=name)
        elif status_type == 'custom':
            # Note: Custom status emoji is not supported in this library version
            activity = discord.CustomActivity(name=name)

        if activity:
            await self.bot.change_presence(activity=activity)
            self.log.info(f"Set status to: {status_type.capitalize()} {name}")
        else:
            self.log.error(f"Invalid status type '{status_type}'. Use 'playing', 'watching', 'listening', or 'custom'.")

    @commands.command(name='join')
    async def join_command(self, ctx, *, invite_code: str):
        """Joins a server using an invite code or URL."""
        invite_code = invite_code.split('/')[-1]
        self.log.info(f"Attempting to join server with invite: {invite_code}...")
        try:
            await self.bot.http.request(
                discord.http.Route('POST', '/invites/{invite_code}', invite_code=invite_code),
                json={}
            )
            self.log.info(f"Successfully sent join request for invite: {invite_code}")
            await ctx.message.edit(content=f"**Result:** Successfully sent join request for invite: `{invite_code}`")
        except discord.CaptchaRequired:
            self.log.warning(f"Failed to join server with invite '{invite_code}': A captcha was required.")
            await ctx.message.edit(
                content=f"**Result:** Failed to join. A captcha was required, which is not supported.")
        except discord.NotFound:
            self.log.error(f"Failed to join. Invite '{invite_code}' is invalid or expired.")
            await ctx.message.edit(content=f"**Result:** Failed to join. Invite `{invite_code}` is invalid or expired.")
        except discord.HTTPException as e:
            self.log.error(
                f"An HTTP error occurred while trying to join invite '{invite_code}'. Status: {e.status}, Code: {e.code}, Response: {e.text}")
            await ctx.message.edit(content=f"**Result:** An HTTP error occurred: {e.status} - {e.text}")
        except Exception:
            self.log.exception(f"An unexpected error occurred in join_command for invite '{invite_code}'.")
            await ctx.message.edit(content=f"**Result:** An unexpected error occurred. Check console for details.")

    @commands.command(name='setstatus')
    async def setstatus_command(self, ctx, status_type: str, *, name: str):
        """Changes the bot's activity status.

        Usage: !setstatus <type> <text>
        Types: playing, watching, listening, custom
        """
        valid_types = ['playing', 'watching', 'listening', 'custom']
        if status_type.lower() not in valid_types:
            await ctx.message.edit(content=f"**Error:** Invalid status type. Use one of: `{'`, `'.join(valid_types)}`.")
            return

        await self.set_status(status_type, name)
        await ctx.message.edit(content=f"**Result:** Status updated to `{status_type.capitalize()}`: `{name}`.")

    @commands.command(name='leave')
    async def leave_command(self, ctx):
        """Leaves the server where the command is used."""
        if not ctx.guild:
            await ctx.message.edit(content="**Error:** This command can only be used in a server.")
            return

        guild_name = ctx.guild.name
        self.log.warning(f"Received command to leave guild: {guild_name} ({ctx.guild.id})")
        await ctx.message.edit(content=f"**Leaving server:** `{guild_name}`...")
        await asyncio.sleep(2)
        await ctx.guild.leave()


async def setup(bot):
    """This is the entry point for loading the cog."""
    await bot.add_cog(ProfileManager(bot))