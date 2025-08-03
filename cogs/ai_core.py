import logging
import discord
import sqlite3
from google import genai
import os
import random
import asyncio
import json
import re
from discord.ext import commands, tasks

def is_self():
    """A check to ensure the command is only used by the bot\'s own account."""
    def predicate(ctx):
        return ctx.author.id == ctx.bot.user.id
    return commands.check(predicate)

class AICore(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log = logging.getLogger(self.__class__.__name__)

        # Load configuration
        self.persona_data = self.bot.config.get('personality_prompt', {})
        self.personality_prompt = self._format_persona_prompt(self.persona_data)
        self.ai_settings = self.bot.config.get("ai_settings", {})

        # Initialize Gemini client
        if not (gemini_api_key := os.getenv("GEMINI_API_KEY")):
            self.log.error("GEMINI_API_KEY not found in .env file. AI Core will not function.")
            self.client = None
        else:
            self.client = genai.Client(api_key=gemini_api_key)
        
        self.chat_model_name = 'gemini-2.5-flash'
        self.summary_model_name = 'gemini-2.5-pro'

        # Database setup
        self.db = sqlite3.connect('data/user_memories.db', check_same_thread=False)
        self._migrate_database() # Ensure the schema is up-to-date
        self.create_memory_table()
        self.create_reaction_log_table()

        # State variables
        self.boredom_level = 0
        self.is_thinking_in_channel = {}
        self.stealth_mode = False

        # Start background tasks
        self.autonomous_message_loop.start()
        self.autonomous_reaction_loop.start()
        self.summarize_memories_loop.start()

    #region Helper Methods
    def _get_server_settings(self, guild_id: int) -> dict:
        server_settings = self.bot.config.get("server_settings", {})
        guild_id_str = str(guild_id)
        return server_settings.get(guild_id_str, server_settings.get("default", {
            "is_active_in_all_channels": False,
            "active_channels": []
        }))

    def _format_persona_prompt(self, persona_data: dict) -> str:
        try:
            p = persona_data['persona_brief']
            name = p['name']
            core = p['core_identity']
            vibe = p['vibe_and_personality']
            speech = p['speech_and_communication_style']
            rambling = speech.get('brevity', speech.get('enthusiastic_rambling', ''))
            slang = speech.get('slang_and_emojis', {})
            topics = p['topics_of_interest']
            example = p.get('example_interaction', [])

            prompt_parts = [
                f"You are a Discord user named {name}. Embody the following persona:",
                f"\n**Core Identity:** {core.get('summary', '')} {core.get('background', '')}",
                f"\n**Personality:** You are the '{vibe.get('summary', '')}'. Your humor is {vibe.get('humor', '')}. Your attitude is {vibe.get('attitude', '')}",
                "\n**Communication Style:**",
                f"- {rambling}" if rambling else "",
                f"- Use slang like: {', '.join(slang.get('examples_slang', []))}.",
                f"- {slang.get('usage', 'Use emojis sparingly.')}",
                f"\n**Interests:** You are deeply invested in {topics.get('gaming', topics.get('gaming_and_media', ''))}. Your hobbies include {topics.get('technology', topics.get('hobbies', ''))}.",
                "\nKeep your responses conversational and relatively short, like a real person would in a chat."
            ]
            
            for key in ['slavic_influence', 'european_influence', 'typing_quirks']:
                if key in speech:
                    for sub_key, value in speech[key].items():
                        if sub_key != 'description':
                            prompt_parts.append(f"- {value}")

            if example:
                example_text = "\n".join([f"{e['speaker']}: {e['line']}" for e in example])
                prompt_parts.append(f"\n**Example Interaction:**\n{example_text}")

            return "\n".join(filter(None, prompt_parts))
        except (KeyError, TypeError) as e:
            self.log.error(f"Could not parse 'personality_prompt' from config.json. Using a default. Error: {e}")
            return "You are a helpful AI assistant."

    def _get_eligible_channels(self, permission_check: str) -> list[discord.TextChannel]:
        target_channels = []
        for guild in self.bot.guilds:
            settings = self._get_server_settings(guild.id)
            if settings.get("is_active_in_all_channels"):
                for channel in guild.text_channels:
                    if getattr(channel.permissions_for(guild.me), permission_check, False):
                        target_channels.append(channel)
            else:
                for channel_id in settings.get("active_channels", []):
                    channel = self.bot.get_channel(channel_id)
                    if channel and channel.guild.id == guild.id and getattr(channel.permissions_for(guild.me), permission_check, False):
                        target_channels.append(channel)
        return target_channels

    def _calculate_typing_delay(self, text: str) -> float:
        config = self.bot.config.get("typing_simulation", {})
        base = config.get("base_delay_seconds", 1.0)
        per_char = config.get("delay_per_char_seconds", 0.04)
        return base + (len(text) * per_char)
    #endregion

    def cog_unload(self):
        self.autonomous_message_loop.cancel()
        self.autonomous_reaction_loop.cancel()
        self.summarize_memories_loop.cancel()
        self.db.close()
        self.log.info("Database connection closed.")
        self.log.info("AICore Cog unloaded.")

    @commands.Cog.listener()
    async def on_ready(self):
        self.log.info("AICore Cog is ready.")

    #region Database Methods
    def _migrate_database(self):
        self.log.info("Checking database schema...")
        cursor = self.db.cursor()
        try:
            # Check if the guild_id column exists
            cursor.execute("SELECT guild_id FROM memories LIMIT 1")
            self.log.info("Database schema is up to date.")
        except sqlite3.OperationalError as e:
            if "no such column: guild_id" in str(e):
                self.log.warning("Outdated database schema detected. Migrating memories table...")
                try:
                    # Begin transaction
                    cursor.execute("BEGIN TRANSACTION")
                    
                    # Step 1: Rename old table
                    cursor.execute("ALTER TABLE memories RENAME TO memories_old")

                    # Step 2: Create new table with the correct schema
                    self.create_memory_table()

                    # Step 3: Copy data from old table to new table, adding a default guild_id of 0 for global memories
                    cursor.execute('''
                        INSERT INTO memories (user_id, guild_id, user_name, notes, relationship_status)
                        SELECT user_id, 0, user_name, notes, relationship_status FROM memories_old
                    ''')

                    # Step 4: Drop the old table
                    cursor.execute("DROP TABLE memories_old")

                    self.db.commit()
                    self.log.info("Database migration successful.")
                except Exception as migration_error:
                    self.log.error(f"Database migration failed: {migration_error}")
                    self.db.rollback() # Rollback changes on failure
                    raise
            else:
                # Some other operational error occurred
                raise e
        finally:
            cursor.close()

    def create_memory_table(self):
        cursor = self.db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memories (
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_name TEXT,
                notes TEXT NOT NULL,
                relationship_status TEXT NOT NULL DEFAULT 'neutral',
                PRIMARY KEY (user_id, guild_id)
            )
        ''')
        self.db.commit()

    def create_reaction_log_table(self):
        cursor = self.db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reacted_messages (
                message_id INTEGER PRIMARY KEY, 
                reacted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.db.commit()

    def get_user_profile(self, user_id: int, user_name: str, guild_id: int = 0) -> tuple[str, str]:
        cursor = self.db.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO memories (user_id, guild_id, user_name, notes) VALUES (?, ?, ?, ?)",
            (user_id, guild_id, user_name, "No memories yet.")
        )
        cursor.execute("SELECT notes, relationship_status FROM memories WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
        result = cursor.fetchone()
        self.db.commit()
        return result if result else ("No memories yet.", "neutral")

    def set_user_notes(self, user_id: int, new_notes: str, guild_id: int = 0):
        cursor = self.db.cursor()
        cursor.execute("UPDATE memories SET notes = ? WHERE user_id = ? AND guild_id = ?", (new_notes, user_id, guild_id))
        self.db.commit()
        self.log.info(f"Set new notes for user {user_id} in context {guild_id}")

    def append_user_memory(self, user_id: int, memory_summary: str, guild_id: int = 0):
        notes, _ = self.get_user_profile(user_id, "Unknown", guild_id=guild_id)
        if "No memories yet." in notes:
            new_notes = f"- {memory_summary}"
        else:
            new_notes = f"{notes}\n- {memory_summary}"
        self.set_user_notes(user_id, new_notes, guild_id=guild_id)
        self.log.info(f"Appended memory for user {user_id} in context {guild_id}")

    def set_user_relationship(self, user_id: int, status: str, guild_id: int = 0):
        cursor = self.db.cursor()
        cursor.execute("UPDATE memories SET relationship_status = ? WHERE user_id = ? AND guild_id = ?", (status, user_id, guild_id))
        self.db.commit()
        self.log.info(f"Updated relationship with user {user_id} to '{status}' in context {guild_id}")
    #endregion

    #region AI Core Logic
    async def _should_respond_to_message(self, message: discord.Message, history: list[discord.Message]) -> bool:
        if not self.client: return False
        
        conversation_log = "\n".join([f"{msg.author.display_name}: {msg.content}" for msg in history[-5:]])
        bot_name = self.persona_data.get('persona_brief', {}).get('name', 'the user')

        prompt = f'''
        You are an AI deciding whether a user should reply to a Discord message. Your name is {bot_name}.
        You have already been mentioned, replied to, or a trigger word was used. The question is not *if* you were addressed, but if it's socially appropriate or interesting for you to reply.

        **Conversation History (last 5 messages):**
        {conversation_log}

        **Analysis Rules:**
        1.  **Is it a simple acknowledgement?** If the last message is just "ok", "lol", "thx", etc., and doesn't ask a question or add new information, you probably shouldn't respond.
        2.  **Is the conversation over?** If the topic seems concluded, it might be awkward to say more.
        3.  **Is it interesting?** Does the message give you an opportunity to be funny, insightful, or continue a topic you care about? If so, you should respond.
        4.  **Is it a command for another bot?** If it looks like a command (e.g., starts with '!', '.'), ignore it.

        **Last Message:** "{message.author.display_name}: {message.content}"

        Based on these rules, should you respond to this message? Answer with a single word: **Yes** or **No**.
        '''
        try:
            response = await self.client.aio.models.generate_content(model=self.chat_model_name, contents=prompt)
            decision = response.text.strip().lower()
            self.log.debug(f"AI response decision: '{decision}'")
            return "yes" in decision
        except Exception as e:
            self.log.error(f"Error in _should_respond_to_message: {e}")
            return False

    async def get_contextual_response(self, message: discord.Message, history: list[discord.Message]):
        if not self.client: return

        context_guild_id = message.guild.id if message.guild else 0
        author_notes, relationship = self.get_user_profile(message.author.id, message.author.display_name, guild_id=context_guild_id)

        conversation_log = "\n".join([f"{msg.author.display_name}: {msg.content}" for msg in history])
        
        prompt = f'''
        {self.personality_prompt}

        **Your Long-Term Memory about {message.author.display_name} (in this server):**
        {author_notes}

        **Your current relationship with {message.author.display_name}:** {relationship}

        ---
        **Full Conversation History:**
        {conversation_log}
        ---

        **Task:** Generate a natural, human-like response to the last message from {message.author.display_name}. Your response should reflect your personality and the context of the conversation.

        - If the conversation provides a new fact about any user, add a newline with `[MEMORIZE] A summary of the new fact.`
        - If the interaction changes your relationship with {message.author.display_name}, add a newline with `[RELATIONSHIP] new_status` (e.g., friendly, wary, helpful, annoyed).
        - Your response should be just the text, without your name or any prefixes.
        '''
        
        try:
            response = await self.client.aio.models.generate_content(model=self.chat_model_name, contents=prompt)
            full_response = response.text.strip()

            reply_text = full_response
            if "[MEMORIZE]" in full_response:
                parts = reply_text.split("[MEMORIZE]")
                reply_text = parts[0].strip()
                memory_summary = parts[1].split("[RELATIONSHIP]")[0].strip()
                if memory_summary:
                    self.append_user_memory(message.author.id, memory_summary, guild_id=context_guild_id)
            
            if "[RELATIONSHIP]" in full_response:
                parts = reply_text.split("[RELATIONSHIP]")
                reply_text = parts[0].strip()
                new_relationship = parts[1].split("[MEMORIZE]")[0].strip()
                if new_relationship:
                    self.set_user_relationship(message.author.id, new_relationship, guild_id=context_guild_id)

            if reply_text:
                async with message.channel.typing():
                    delay = self._calculate_typing_delay(reply_text)
                    await asyncio.sleep(delay)
                    await message.reply(reply_text)
                    self.boredom_level = 0

        except Exception as e:
            self.log.error(f"An error occurred while generating a response: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.id == self.bot.user.id or message.author.bot:
            return
        
        if message.author.id in self.bot.config.get('ignored_users', []):
            return

        if message.guild:
            server_settings = self._get_server_settings(message.guild.id)
            if not server_settings.get("is_active_in_all_channels") and message.channel.id not in server_settings.get("active_channels", []):
                return
        
        if self.is_thinking_in_channel.get(message.channel.id):
            return

        # --- RELIABLE CHECK to see if the bot is being addressed ---
        is_mentioned = self.bot.user in message.mentions
        is_reply = message.reference and message.reference.resolved and message.reference.resolved.author.id == self.bot.user.id
        
        triggered = False
        trigger_words = self.bot.config.get('trigger_words', [])
        if trigger_words:
            # Use regex for whole word matching, case-insensitive
            if any(re.search(r'\b' + re.escape(word) + r'\b', message.content, re.IGNORECASE) for word in trigger_words):
                triggered = True

        # If not addressed, ignore the message.
        if not (is_mentioned or is_reply or triggered):
            return

        # --- Now, proceed with the AI decision logic ---
        try:
            self.is_thinking_in_channel[message.channel.id] = True
            
            history_limit = self.ai_settings.get("chat_history_limit", 20)
            history = [msg async for msg in message.channel.history(limit=history_limit)]
            history.reverse()

            # Call the AI to get its opinion on whether to respond
            if await self._should_respond_to_message(message, history):
                await self.get_contextual_response(message, history)
        finally:
            self.is_thinking_in_channel.pop(message.channel.id, None)
    #endregion

    #region Commands
    @commands.command(name='stealth')
    @is_self()
    async def stealth_command(self, ctx, mode: str):
        mode = mode.lower()
        if mode == 'on':
            self.stealth_mode = True
            await ctx.message.edit(content="**Stealth Mode:** Activated.")
            self.log.info("Stealth Mode has been enabled.")
        elif mode == 'off':
            self.stealth_mode = False
            await ctx.message.edit(content="**Stealth Mode:** Deactivated.")
            self.log.info("Stealth Mode has been disabled.")
        else:
            await ctx.message.edit(content="**Error:** Invalid mode. Use `!stealth on` or `!stealth off`.")
            
    @commands.group(name='memory', invoke_without_command=True)
    @is_self()
    async def memory_command(self, ctx):
        await ctx.message.edit(content="**Memory Command Group**\nUse `!memory <subcommand> <user> [args]`\n\n**Subcommands:** `view`, `add`, `clear`")

    @memory_command.command(name='view')
    @is_self()
    async def memory_view(self, ctx, user: discord.User):
        guild_id = ctx.guild.id if ctx.guild else 0
        notes, relationship = self.get_user_profile(user.id, user.display_name, guild_id=guild_id)
        context_str = f"in `{ctx.guild.name}`" if ctx.guild else "globally"
        embed = discord.Embed(title=f"Memory Profile for {user.display_name}", description=f"This is the memory profile for this user {context_str}.", color=discord.Color.blue())
        embed.add_field(name="Relationship Status", value=relationship.capitalize(), inline=False)
        embed.add_field(name="Memory Notes", value=notes, inline=False)
        await ctx.message.edit(content="", embed=embed)

    @memory_command.command(name='add')
    @is_self()
    async def memory_add(self, ctx, user: discord.User, *, text: str):
        guild_id = ctx.guild.id if ctx.guild else 0
        self.append_user_memory(user.id, text, guild_id=guild_id)
        context_str = f"in `{ctx.guild.name}`" if ctx.guild else "globally"
        await ctx.message.edit(content=f"**Success:** Added memory for **{user.display_name}** {context_str}.")

    @memory_command.command(name='clear')
    @is_self()
    async def memory_clear(self, ctx, user: discord.User):
        guild_id = ctx.guild.id if ctx.guild else 0
        self.set_user_notes(user.id, "No memories yet.", guild_id=guild_id)
        context_str = f"in `{ctx.guild.name}`" if ctx.guild else "globally"
        await ctx.message.edit(content=f"**Success:** Cleared memory notes for **{user.display_name}** {context_str}.")

    #region Active Channels Commands
    async def _resolve_guild(self, identifier: str) -> discord.Guild | None:
        try:
            guild_id = int(identifier)
            guild = self.bot.get_guild(guild_id)
            if guild: return guild
        except (ValueError, TypeError): pass
        return discord.utils.get(self.bot.guilds, name=identifier)

    async def _parse_guild_and_channels(self, ctx, args: list[str]) -> tuple[discord.Guild | None, list[str]]:
        if not ctx.guild:
            if not args:
                await ctx.message.edit(content="**Error:** You must specify a server name or ID when using this command in DMs.")
                return None, []
            guild = await self._resolve_guild(args[0])
            if not guild:
                await ctx.message.edit(content=f"**Error:** Could not find a server with the name or ID: `{args[0]}`")
                return None, []
            return guild, args[1:]
        
        if args:
            # Check if the first arg is a guild, allowing override in a server
            potential_guild = await self._resolve_guild(args[0])
            if potential_guild:
                return potential_guild, args[1:]

        return ctx.guild, args

    def _resolve_channel_ids(self, guild: discord.Guild, identifiers: list[str]) -> tuple[list[int], list[str]]:
        channel_ids, failed = [], []
        for identifier in identifiers:
            try:
                if identifier.startswith('<#') and identifier.endswith('>'):
                    channel_id = int(identifier[2:-1])
                    channel = guild.get_channel(channel_id)
                else:
                    channel = discord.utils.get(guild.text_channels, name=identifier) or guild.get_channel(int(identifier))
                
                if channel: channel_ids.append(channel.id)
                else: failed.append(identifier)
            except (ValueError, TypeError):
                failed.append(identifier)
        return channel_ids, failed

    @commands.group(name='activechannels', invoke_without_command=True)
    @is_self()
    async def activechannels_command(self, ctx, *, guild_identifier: str = None):
        target_guild = ctx.guild
        if guild_identifier:
            target_guild = await self._resolve_guild(guild_identifier)
        
        if not target_guild:
            return await ctx.message.edit(content="**Error:** Guild not found.")

        settings = self._get_server_settings(target_guild.id)
        if settings.get("is_active_in_all_channels"):
            return await ctx.message.edit(content=f"**Status for `{target_guild.name}`:** Active in **all** channels.")
            
        channel_ids = settings.get('active_channels', [])
        if not channel_ids:
            return await ctx.message.edit(content=f"**Status for `{target_guild.name}`:** No active channels set.")
            
        mentions = [f"<#{cid}>" for cid in channel_ids if self.bot.get_channel(cid)]
        await ctx.message.edit(content=f"**Active Channels for `{target_guild.name}` ({len(mentions)}):**\n" + "\n".join(mentions))

    @activechannels_command.command(name='set')
    @is_self()
    async def activechannels_set(self, ctx, *args):
        target_guild, channel_identifiers = await self._parse_guild_and_channels(ctx, list(args))
        if not target_guild: return
        if not channel_identifiers: return await ctx.message.edit(content="**Error:** You must provide at least one channel.")

        channel_ids, failed = self._resolve_channel_ids(target_guild, channel_identifiers)
        if not channel_ids: return await ctx.message.edit(content=f"**Error:** No valid channels found for `{target_guild.name}`.")

        guild_id_str = str(target_guild.id)
        if 'server_settings' not in self.bot.config: self.bot.config['server_settings'] = {}
        self.bot.config['server_settings'][guild_id_str] = {'active_channels': channel_ids, 'is_active_in_all_channels': False}
        self.bot.save_config()

        response = f"**Success:** Bot activity in `{target_guild.name}` is now restricted to {len(channel_ids)} channel(s)."
        if failed: response += f"\n**Note:** Could not resolve: `{', '.join(failed)}`"
        await ctx.message.edit(content=response)

    @activechannels_command.command(name='add')
    @is_self()
    async def activechannels_add(self, ctx, *args):
        target_guild, channel_identifiers = await self._parse_guild_and_channels(ctx, list(args))
        if not target_guild: return
        if not channel_identifiers: return await ctx.message.edit(content="**Error:** You must provide at least one channel to add.")

        channel_ids, failed = self._resolve_channel_ids(target_guild, channel_identifiers)
        if not channel_ids: return await ctx.message.edit(content=f"**Error:** Could not resolve any of the specified channels.")

        guild_id_str = str(target_guild.id)
        settings = self._get_server_settings(target_guild.id)
        current_ids = set(settings.get('active_channels', []))
        added_count = len(set(channel_ids) - current_ids)
        current_ids.update(channel_ids)

        if 'server_settings' not in self.bot.config: self.bot.config['server_settings'] = {}
        self.bot.config['server_settings'][guild_id_str] = {'active_channels': list(current_ids), 'is_active_in_all_channels': False}
        self.bot.save_config()

        response = f"**Success:** Added {added_count} channel(s) to `{target_guild.name}`. Total active: {len(current_ids)}."
        if failed: response += f"\n**Note:** Could not resolve: `{', '.join(failed)}`"
        await ctx.message.edit(content=response)

    @activechannels_command.command(name='remove')
    @is_self()
    async def activechannels_remove(self, ctx, *args):
        target_guild, channel_identifiers = await self._parse_guild_and_channels(ctx, list(args))
        if not target_guild: return
        if not channel_identifiers: return await ctx.message.edit(content="**Error:** You must provide at least one channel to remove.")

        channel_ids, failed = self._resolve_channel_ids(target_guild, channel_identifiers)
        if not channel_ids: return await ctx.message.edit(content=f"**Error:** Could not resolve any of the specified channels.")

        guild_id_str = str(target_guild.id)
        settings = self._get_server_settings(target_guild.id)
        current_ids = set(settings.get('active_channels', []))
        removed_count = len(current_ids.intersection(channel_ids))
        current_ids.difference_update(channel_ids)

        if 'server_settings' not in self.bot.config: self.bot.config['server_settings'] = {}
        self.bot.config['server_settings'][guild_id_str] = {'active_channels': list(current_ids), 'is_active_in_all_channels': False}
        self.bot.save_config()

        response = f"**Success:** Removed {removed_count} channel(s) from `{target_guild.name}`. Total active: {len(current_ids)}."
        if failed: response += f"\n**Note:** Could not resolve: `{', '.join(failed)}`"
        await ctx.message.edit(content=response)

    @activechannels_command.command(name='all')
    @is_self()
    async def activechannels_all(self, ctx, *, guild_identifier: str = None):
        target_guild = ctx.guild
        if guild_identifier: target_guild = await self._resolve_guild(guild_identifier)
        if not target_guild: return await ctx.message.edit(content="**Error:** Guild not found.")

        guild_id_str = str(target_guild.id)
        if 'server_settings' not in self.bot.config: self.bot.config['server_settings'] = {}
        self.bot.config['server_settings'][guild_id_str] = {'is_active_in_all_channels': True, 'active_channels': []}
        self.bot.save_config()
        await ctx.message.edit(content=f"**Success:** Bot is now active in **all** channels in `{target_guild.name}`.")

    @activechannels_command.command(name='clear')
    @is_self()
    async def activechannels_clear(self, ctx, *, guild_identifier: str = None):
        target_guild = ctx.guild
        if guild_identifier: target_guild = await self._resolve_guild(guild_identifier)
        if not target_guild: return await ctx.message.edit(content="**Error:** Guild not found.")

        guild_id_str = str(target_guild.id)
        if 'server_settings' not in self.bot.config: self.bot.config['server_settings'] = {}
        self.bot.config['server_settings'][guild_id_str] = {'is_active_in_all_channels': False, 'active_channels': []}
        self.bot.save_config()
        await ctx.message.edit(content=f"**Success:** Cleared all active channels for `{target_guild.name}`.")
    #endregion
    #endregion

    #region Background Tasks
    @tasks.loop(minutes=1.0)
    async def autonomous_message_loop(self):
        if self.stealth_mode or not self.client: return
        
        self.boredom_level += 1
        boredom_threshold = self.bot.config.get("boredom_threshold", 120)
        if self.boredom_level < boredom_threshold:
            return

        target_channels = self._get_eligible_channels('send_messages')
        if not target_channels:
            self.log.warning("Autonomous message triggered, but no eligible channels found.")
            return

        channel = random.choice(target_channels)
        self.log.info(f"Boredom triggered! Attempting to send a message to #{channel.name} in {channel.guild.name}")
        
        history = [msg async for msg in channel.history(limit=5)]
        conversation_log = "\n".join([f"{msg.author.display_name}: {msg.content}" for msg in reversed(history)])
        prompt = f"{self.personality_prompt}\n\nYou're feeling bored and want to start a conversation. Based on the last few messages, say something interesting or ask a question.\n\nRecent Messages:\n{conversation_log}"

        try:
            response = await self.client.aio.models.generate_content(model=self.chat_model_name, contents=prompt)
            if response.text:
                message_text = response.text.strip()
                async with channel.typing():
                    delay = self._calculate_typing_delay(message_text)
                    await asyncio.sleep(delay)
                    await channel.send(message_text)
                self.boredom_level = 0
                self.log.info(f"Sent autonomous message to #{channel.name}.")
        except Exception as e:
            self.log.error(f"Failed to send autonomous message: {e}")

    @tasks.loop(hours=24)
    async def summarize_memories_loop(self):
        if not self.client: return
        self.log.info("Starting daily memory summarization...")
        cursor = self.db.cursor()
        cursor.execute("SELECT user_id, guild_id, notes FROM memories")
        threshold = self.ai_settings.get("memory_summarization_threshold", 1500)

        for user_id, guild_id, notes in cursor.fetchall():
            if len(notes) > threshold:
                self.log.info(f"Summarizing memories for user {user_id} in context {guild_id}...")
                prompt = f"Summarize these notes about a user into a concise, bulleted list:\n\n{notes}"
                try:
                    response = await self.client.aio.models.generate_content(model=self.summary_model_name, contents=prompt)
                    if response.text:
                        self.set_user_notes(user_id, response.text.strip(), guild_id=guild_id)
                except Exception as e:
                    self.log.error(f"Failed to summarize memories for user {user_id}: {e}")
        self.log.info("Memory summarization complete.")
        
    @tasks.loop(minutes=5.0)
    async def autonomous_reaction_loop(self):
        if self.stealth_mode or not self.client: return
        
        try:
            target_channels = self._get_eligible_channels('add_reactions')
            if not target_channels: return

            channel = random.choice(target_channels)
            cursor = self.db.cursor()

            async for message in channel.history(limit=10):
                cursor.execute("SELECT 1 FROM reacted_messages WHERE message_id = ?", (message.id,))
                if cursor.fetchone(): continue

                prompt = f"{self.personality_prompt}\n\nRead the following message and decide on a single, appropriate emoji reaction. Your response must be ONLY the emoji itself.\n\nMessage: \"{message.content}\""
                response = await self.client.aio.models.generate_content(model=self.chat_model_name, contents=prompt)
                reaction_emoji = response.text.strip()

                if reaction_emoji:
                    await message.add_reaction(reaction_emoji)
                    self.log.info(f"Reacted to message {message.id} in #{channel.name} with {reaction_emoji}")
                    cursor.execute("INSERT INTO reacted_messages (message_id) VALUES (?)", (message.id,))
                    self.db.commit()
                    break # Only react to one message per loop cycle
        except Exception as e:
            self.log.error(f"Error in autonomous_reaction_loop: {e}")
    #endregion

async def setup(bot):
    await bot.add_cog(AICore(bot))
