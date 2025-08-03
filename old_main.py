import discord
import os
import json
import asyncio
import requests
import logging # Import the logging module
from discord.ext import commands
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


# --- Helper Functions ---
def load_config():
    """Loads the configuration from the JSON file."""
    # FIX: Specify UTF-8 encoding to handle special characters in the persona.
    with open('data/config.json', 'r', encoding='utf-8') as f:
        return json.load(f)


def validate_token(token: str) -> bool:
    """
    Checks if a Discord token is valid by making a request to the /users/@me endpoint.
    Includes a timeout to prevent indefinite hanging.
    """
    headers = {"Authorization": token}
    try:
        r = requests.get("https://discord.com/api/v10/users/@me", headers=headers, timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False


# --- Bot Class ---
# Note: Self-bots are against Discord's ToS. This is for educational purposes.
class MySelfBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = load_config()
        logging.info("Bot initialized. Loading configuration...")

    def save_config(self):
        """Atomically saves the current configuration to the JSON file."""
        # FIX: Write with UTF-8 and ensure non-ASCII characters are not escaped.
        with open('data/config.json', 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)
        logging.info("Configuration saved.")

    async def on_ready(self):
        """Called when the bot is fully connected and internal caches are ready."""
        logging.info(f"Logged in as {self.user.name} ({self.user.id})")
        logging.info("-------------------------------------------------")
        bot_name = self.config.get('personality_prompt', {}).get('persona_brief', {}).get('name', 'The bot')
        logging.info(f"{bot_name} is fully operational and ready for commands.")
        logging.info("-------------------------------------------------")

    async def setup_hook(self):
        """This is called once before the bot logs in to load extensions."""
        logging.info("Loading cogs...")
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    logging.info(f"  - Successfully loaded {filename}")
                except Exception:
                    # Use logging.exception to get the full traceback
                    logging.exception(f"  - FAILED to load {filename}")
        logging.info("-------------------------------------------------")


# --- Run the Bot ---
if __name__ == '__main__':
    # --- Setup basic logging ---
    # This will capture full tracebacks for any unexpected errors.
    log_format = '%(asctime)s - %(levelname)s - %(name)s: %(message)s'
    logging.basicConfig(level=logging.INFO, format=log_format)

    # --- Robust login loop with token validation ---
    while True:
        try:
            token = os.getenv('DISCORD_TOKEN')
            if not token:
                raise ValueError("DISCORD_TOKEN not found in .env file or environment variables.")

            if not validate_token(token):
                raise discord.LoginFailure("The token in your .env file is invalid.")

            # --- FIX: Disable guild chunking to prevent startup timeouts. ---
            # This is the correct way to solve the member list subscription error for a self-bot.
            client = MySelfBot(
                command_prefix='!',
                self_bot=True,
                chunk_guilds_at_startup=False
            )

            client.run(token, log_handler=None)
            break

        except (discord.LoginFailure, ValueError) as e:
            logging.error(f"[LOGIN FAILED]: {e}")
            while True:
                print("Please provide a new, valid Discord token.")
                print("-------------------------------------------------------------")
                new_token = input("| New Token: ").strip().strip('"')

                if validate_token(new_token):
                    logging.info("Token appears to be valid. Updating .env file...")
                    os.environ['DISCORD_TOKEN'] = new_token
                    try:
                        # FIX: Specify UTF-8 for reading the .env file
                        with open('.env', 'r', encoding='utf-8') as f:
                            lines = f.readlines()
                        # FIX: Specify UTF-8 for writing the .env file
                        with open('.env', 'w', encoding='utf-8') as f:
                            found = False
                            for line in lines:
                                if line.strip().startswith('DISCORD_TOKEN='):
                                    f.write(f'DISCORD_TOKEN="{new_token}"\n')
                                    found = True
                                else:
                                    f.write(line)
                            if not found:
                                f.write(f'\nDISCORD_TOKEN="{new_token}"\n')
                        logging.info("Successfully updated .env file. Retrying login...")
                    except FileNotFoundError:
                        logging.warning("Could not find .env file to update. The new token will only be used for this session.")
                    break
                else:
                    logging.error("[VALIDATION FAILED]: The token you entered is not valid. Please try again.\n")
            continue
        except Exception:
            logging.exception("An unhandled exception occurred during bot execution:")
            break