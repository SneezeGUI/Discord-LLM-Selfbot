# Discord LLM Self-Bot

This is a sophisticated, conversational AI self-bot for Discord, powered by Google's Gemini large language model. It's designed to emulate a specific personality, engage in context-aware conversations, and learn from interactions.

---

## ⚠️ Important Warning: Self-Bots Violate Discord's ToS

Using a self-bot is a direct violation of Discord's Terms of Service and can result in the **permanent termination of your account**. The Discord API is not intended for user account automation.

- **Use this software at your own extreme risk.**
- **Do not use this on an account you care about.** It is safest to use this only on a secondary, throwaway account.
- The creator of this project is not responsible for any action taken against your account.

---

## Features

- **Conversational AI**: Utilizes Google's Gemini Pro model to generate human-like, contextual responses.
- **Customizable Personality**: Define a detailed persona in `config.json` to give the bot a unique voice, humor, and style.
- **Context-Aware Memory**: The bot remembers recent messages in a channel to understand the flow of conversation.
- **Long-Term Memory**: Automatically summarizes conversations and stores key details in a local database (`user_memories.db`) to build a long-term understanding of users and topics.
- **Flexible Triggers**: Responds to @mentions, direct replies, and configurable trigger words.
- **Typing Simulation**: Simulates realistic typing delays to appear more natural.
- **Configurable**: Easily change settings like trigger words, ignored users, AI model versions, and more.

## Prerequisites

- [Python 3.10+](https://www.python.org/downloads/)
- [Git](https://git-scm.com/downloads/)
- A secondary, non-essential Discord account.
- A Google AI API Key.

## Installation & Configuration

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd Discord-LLM-Selfbot
    ```

2.  **Create a virtual environment and install dependencies:**
    ```bash
    # Create the virtual environment
    python -m venv .venv

    # Activate it
    # On Windows:
    .venv\Scripts\activate
    # On macOS/Linux:
    source .venv/bin/activate

    # Install required packages
    pip install -r requirements.txt
    ```

3.  **Set up your environment variables:**
    - Rename the `.env.example` file to `.env`.
    - Open the `.env` file and add your Discord Token and Google API Key.
      ```ini
      # Your private Discord user token.
      # WARNING: Do NOT share this with anyone.
      DISCORD_TOKEN=your_discord_token_here

      # Your API key for the Google Generative AI service (Gemini).
      GOOGLE_API_KEY=your_google_api_key_here
      ```
    - **To get your Discord Token:** Log into your secondary Discord account in a web browser, open the developer tools (F12), and look for the token in the headers of network requests. Be aware that this process is sensitive and exposing your token is a major security risk.

4.  **Configure the Bot:**
    - Open `data/config.json` to customize the bot's behavior.
    - **`personality_prompt`**: This is the core of your bot's identity. Modify the name, background, humor, and speech style to create the personality you want.
    - **`trigger_words`**: Add a list of case-insensitive words or phrases that will make the bot consider responding.
    - **`ai_settings`**: Control which Gemini models are used and set conversation history limits.

## Usage

Once everything is configured, run the bot from your activated virtual environment:

```bash
python main.py
```

The bot will log in and print a confirmation to the console. It is now active and will respond according to your configuration.

---

## Disclaimer

This project is for educational and experimental purposes only. The developers assume no liability for any consequences resulting from the use of this software, including but not limited to account termination.
