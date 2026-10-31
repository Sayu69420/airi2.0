# Airi 2.0 Discord Bot

[![Author: Neon Furry](https://img.shields.io/badge/Author-Neon_Furry-ff69b4?style=for-the-badge&logo=discord&logoColor=white)](https://github.com/Sayu69420)
[![GitHub Repository](https://img.shields.io/badge/GitHub-Sayu69420%2Fairi2.0-blue?style=for-the-badge&logo=github&logoColor=white)](https://github.com/Sayu69420/airi2.0)
[![Invite Airi](https://img.shields.io/badge/Invite-Airi_Bot-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1532368781094162472&permissions=8&integration_type=0&scope=bot%20applications.commands)
[![Install Everywhere](https://img.shields.io/badge/Install-Everywhere_(User_App)-57F287?style=for-the-badge&logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1532368781094162472&integration_type=1&scope=applications.commands)

A multi-purpose Discord bot (discord.py 2.7, cog-based) created by **Neon Furry** with Discord Slash Application Commands & User Install support (Apps Everywhere!), music streaming, an AI companion inspired by `moeru-ai/airi`, Reddit/meme fetching, anime/manga lookup, and moderation.

Source code: **[https://github.com/Sayu69420/airi2.0](https://github.com/Sayu69420/airi2.0)**

## 🔗 Add Airi to Discord

You can add Airi to your Discord in two ways:

1. 🏠 **[Add Airi to a Discord Server (Guild)](https://discord.com/oauth2/authorize?client_id=1532368781094162472&permissions=8&integration_type=0&scope=bot%20applications.commands)**
   - For full server features: music voice streaming, moderation, server chat, and guild slash commands.
2. 🌐 **[Install Airi to Your Account / Profile (Use Everywhere!)](https://discord.com/oauth2/authorize?client_id=1532368781094162472&integration_type=1&scope=applications.commands)**
   - **Discord User App**: Members can install Airi directly to their personal Discord accounts and run slash commands (`/chat`, `/imagine`, `/ping`, `/anime`, `/pic`, etc.) **everywhere across Discord** — in direct messages, group chats, and any server even if the bot is not invited there!

### ⚙️ Enabling "User Install" in Discord Developer Portal
To allow members to install Airi to their user profiles and use it everywhere:
1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Select your application (**Airi**).
3. In the sidebar, navigate to **Installation**.
4. Under **Installation Contexts**, ensure **User Install** is checked (along with Guild Install).
5. Under **Default Install Settings**, set:
   - Scopes: `applications.commands` and `bot`
6. Save Changes.

## Features

- **Slash Application & User App Everywhere** — Full Discord slash command integration (`/`). Configured with `AppInstallationType.user` and all command contexts (`guild`, `dm_channel`, `private_channel`) so members can use slash commands anywhere on Discord.
- **AI companion & Art** — chats via `/chat`, `.chat`, mention, reply, or DM. Tries providers in order: a local/custom OpenAI-compatible server, NVIDIA NIM, OpenRouter, Groq, OpenAI, Google Gemini, Ollama, and [AI Horde](https://aihorde.net). Supports crowdsourced text LLMs (`/horde`) and Stable Diffusion art generation (`/imagine`). Conversation history persists per chat across restarts.
- **Music** — play from YouTube or Spotify links (Spotify resolves to track names and streams from YouTube), queueing, looping, shuffle, and cloud liked songs.
- **Reddit** — random memes (`/meme`), subreddit browsing (`/reddit`), NSFW image fetching.
- **Anime & Manga** — lookup via Jikan (`/anime`, `/manga`), no API key required.
- **Avatar & Media** — profile picture download in HD/GIF (`/pic`), wallpapers (`/wallpaper`).
- **Moderation & Utility** — latency (`/ping`), AFK status (`/afk`), purge, mute, prefix configuration.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.sample .env
```

Fill in `.env`:

- `DISCORD_BOT_TOKEN` is required — get one at the
  [Discord Developer Portal](https://discord.com/developers/applications).
  On that app's page, enable the **MESSAGE CONTENT** privileged intent
  (Bot → Privileged Gateway Intents) for prefix command support.
- Optional keys: `AIHORDE_API_KEY`, `GEMINI_API_KEY`, `NVIDIA_API_KEY`, `SPOTIFY_CLIENT_ID`, `REDDIT_CLIENT_ID`.

Run it:

```bash
.venv/bin/python main.py
```

Slash commands will automatically sync with Discord on startup. The bot owner can also run `.sync` anytime.

## Commands

Airi supports both **Slash Commands (`/`)** and prefix commands (`.`):

| Feature | Slash Command | Prefix Command | Description | Works Everywhere? |
|---|---|---|---|---|
| AI Companion | `/chat <prompt>` | `.chat <prompt>` | Chat with Airi AI companion | ✅ Yes |
| Google Gemini | `/gemini <prompt>` | `.gemini <prompt>` | Query Google Gemini AI | ✅ Yes |
| NVIDIA NIM | `/nvidia <prompt>` | `.nvidia <prompt>` | Query NVIDIA NIM AI | ✅ Yes |
| AI Horde LLM | `/horde <prompt>` | `.horde <prompt>` | Chat with decentralized LLMs | ✅ Yes |
| AI Image Art | `/imagine <prompt>` | `.imagine <prompt>` | Stable Diffusion image generator | ✅ Yes |
| AI Horde Kudos | `/kudos` | `.kudos` | Check Kudos balance & workers | ✅ Yes |
| AI Horde Models | `/horde_models` | `.horde_models` | List active text & image models | ✅ Yes |
| AI Status | `/ai_status` | `.ai_status` | Check AI engine & memory status | ✅ Yes |
| Reset AI Chat | `/ai_reset` | `.ai_reset` | Clear chat memory in this channel | ✅ Yes |
| Bot Latency | `/ping` | `.ping` | Check bot response latency | ✅ Yes |
| Hello Airi | `/hi` | `.hi` | Friendly greeting from Airi | ✅ Yes |
| About & Creator | `/about` | `.about` / `.creator` | About Airi and author Neon Furry | ✅ Yes |
| Anime Search | `/anime <name>` | `.anime <name>` | Search MyAnimeList database | ✅ Yes |
| Manga Search | `/manga <name>` | `.manga <name>` | Search Manga database | ✅ Yes |
| Avatar Viewer | `/pic [user]` | `.pic <user>` | View user's HD / animated avatar | ✅ Yes |
| Wallpapers | `/wallpaper [query]` | `.wall [query]` | Get high-resolution wallpapers | ✅ Yes |
| Reddit Memes | `/meme` | `.meme` | Random top memes | ✅ Yes |
| Subreddit | `/reddit <subName>` | `.r <subName>` | Fetch top posts from subreddits | ✅ Yes |
| AFK Status | `/afk [reason]` | `.afk [reason]` | Set or clear AFK message | ✅ Yes |
| Bot Invites | `/botinvite` | `.botinvite` | Get Server and User App install links | ✅ Yes |
| Help Directory | `/help [category]` | `.help [category]` | Interactive help menu and guide | ✅ Yes |
| Music Playback | - | `.p <song/url>`, `.s`, `.q` | Stream music (YouTube & Spotify) | 🏠 Servers only |
| Server Admin | - | `.mute`, `.purge`, `.prefix` | Server moderation | 🏠 Servers only |
| Sync (Owner) | - | `.sync [guild\|clear]` | Sync slash commands with Discord | 👑 Owner only |

## 👑 Author & Credits

- **Author / Creator:** **Neon Furry** 🐾
- **GitHub Repository:** [Sayu69420/airi2.0](https://github.com/Sayu69420/airi2.0)
- **Inspiration:** [moeru-ai/airi](https://github.com/moeru-ai/airi)
- **Framework:** [discord.py](https://github.com/Rapptz/discord.py) v2.7+
