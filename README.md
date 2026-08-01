# Airi 2.0 Discord Bot

A multi-purpose Discord bot (discord.py 2.4, cog-based) with music playback,
an AI companion, Reddit/meme fetching, anime/manga lookup, and basic
moderation.

## Features

- **Music** — play from YouTube or Spotify links (Spotify resolves to track
  names and streams the match from YouTube; audio is never pulled from
  Spotify directly), queueing, looping, shuffle, and a per-user liked-songs
  list.
- **AI companion** — chats via mention, reply, or DM. Tries providers in
  order: a local/custom OpenAI-compatible server, NVIDIA NIM, OpenRouter,
  Groq, OpenAI, Google Gemini, Ollama — falling back to canned replies if
  none are configured. Conversation history persists per channel across
  restarts. Has a separate NSFW mode, gated to NSFW channels/DMs.
- **Reddit** — random memes, subreddit browsing, NSFW image fetching (NSFW
  channels only).
- **Anime & Manga** — lookup via Jikan (MyAnimeList), no API key required.
- **Moderation & utility** — mute, purge, AFK status, invites, bug reports,
  per-guild command prefix.

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
  (Bot → Privileged Gateway Intents), or the bot will connect but ignore
  every prefix command.
- Everything else in `.env.sample` is optional and degrades gracefully:
  no AI provider key → canned replies only; no Spotify keys → slower embed
  scraping instead of the API; no Reddit keys → Reddit commands reply with a
  setup hint instead of running.

Run it:

```bash
.venv/bin/python main.py
```

## Commands

Default prefix is `.` (also works via bot mention). Full list in-bot via
`.help` and `.help commands`.

| Category | Examples |
|---|---|
| Music | `.p <song/url>`, `.pause`, `.np`, `.queue`, `.loop`, `.like` |
| AI | `.chat <message>`, `@Airi <message>`, `.ai_status`, `.ai_reset` |
| Reddit | `.meme`, `.r <sub>`, `.nsfw` |
| Fun | `.anime <name>`, `.manga <name>`, `.wall`, `.pic <@member>` |
| Server | `.prefix`, `.mute`, `.purge`, `.afk`, `.bug <report>` |

## Data

A single SQLite database, `airi.db`, is created automatically on first run
(gitignored) and holds per-guild prefixes, liked songs, and AI chat history.
No manual setup needed.
