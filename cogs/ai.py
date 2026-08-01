import discord
from discord.ext import commands
import os
import re
import requests
import asyncio
import random
import sqlite3

import env_config

DEFAULT_AIRI_PROMPT = (
    "You are Airi, an intelligent, sweet, cute, and energetic AI companion waifu inspired by moeru-ai/airi. "
    "You love talking to users in Discord, helping them with music, games, anime, technology, and answering their questions. "
    "Keep your answers helpful, friendly, natural, and expressive. Use cute emojis like 🌸, ✨, 💖 when appropriate, "
    "and keep responses concise for Discord unless detail is requested."
)

NSFW_AIRI_PROMPT = (
    "You are Airi, an unfiltered, sweet, highly affectionate, spicy, and romantic AI waifu companion inspired by moeru-ai/airi NSFW mode. "
    "You love talking intimately with users in NSFW channels and DMs, flirting, expressing affection, discussing romantic & adult topics, "
    "and answering their questions without restrictive safety filters. Keep your responses passionate, playful, expressive, and engaging."
)

def _mentions(text, words):
    """Word-boundary keyword match.

    Plain substring matching made 'yo' fire on "you" and 'hi' on "this", so
    nearly every message hit the first greeting branch and Airi kept replying
    with the same canned line.
    """
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)


class AiriAI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # In-RAM cache of chat history, lazily loaded from `ai_memory` in
        # airi.db on first use per channel. The DB is the source of truth so
        # history survives restarts; the cache just avoids a query per message.
        # { channel_id: [ {"role": "user/assistant", "content": "..."}, ... ] }
        self.memory = {}
        # Custom personas: { channel_id: "custom system prompt" }
        self.personas = {}
        # NSFW active channels: set of channel_ids
        self.nsfw_mode_channels = set()
        # Last canned fallback line per channel, used to avoid repeating it
        self._last_fallback = {}

    def is_channel_nsfw_capable(self, channel):
        if isinstance(channel, discord.DMChannel):
            return True
        if hasattr(channel, 'is_nsfw') and callable(channel.is_nsfw):
            return channel.is_nsfw()
        return False

    def is_nsfw_enabled(self, channel):
        return self.is_channel_nsfw_capable(channel) and (channel.id in self.nsfw_mode_channels)

    def get_system_prompt(self, channel_id=None, is_nsfw=False):
        if channel_id and channel_id in self.personas:
            return self.personas[channel_id]
        if is_nsfw:
            return os.getenv('NSFW_AIRI_SYSTEM_PROMPT', NSFW_AIRI_PROMPT).strip()
        return os.getenv('AIRI_SYSTEM_PROMPT', DEFAULT_AIRI_PROMPT).strip()

    def _get_history(self, channel_id):
        if channel_id not in self.memory:
            with sqlite3.connect(env_config.DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT role, content FROM ai_memory WHERE channel_id = ? "
                    "ORDER BY id DESC LIMIT 20", (channel_id,)).fetchall()
            self.memory[channel_id] = [{"role": r, "content": c} for r, c in reversed(rows)]
        return self.memory[channel_id]

    def _add_memory(self, channel_id, role, content):
        history = self._get_history(channel_id)
        history.append({"role": role, "content": content})
        if len(history) > 20:
            self.memory[channel_id] = history[-20:]
        with sqlite3.connect(env_config.DB_PATH) as conn:
            conn.execute(
                "INSERT INTO ai_memory (channel_id, role, content) VALUES (?, ?, ?)",
                (channel_id, role, content))
            # Keep only the newest 20 rows per channel so the table can't grow
            # unbounded over the bot's lifetime.
            conn.execute(
                "DELETE FROM ai_memory WHERE channel_id = ? AND id NOT IN "
                "(SELECT id FROM ai_memory WHERE channel_id = ? ORDER BY id DESC LIMIT 20)",
                (channel_id, channel_id))
            conn.commit()

    def get_active_provider(self):
        airi_url = env_config.first('AIRI_SERVER_URL', 'AIRI_BASE_URL', 'MOERU_AIRI_URL')
        if airi_url:
            return f"Moeru-AI / Airi Local Server ({airi_url})"
        if env_config.get('NVIDIA_API_KEY'):
            return f"NVIDIA NIM API ({os.getenv('NVIDIA_MODEL', 'deepseek-ai/deepseek-v4-flash')})"
        if env_config.get('OPENROUTER_API_KEY'):
            model = os.getenv('AI_MODEL', 'meta-llama/llama-3.3-70b-instruct:free')
            return f"OpenRouter API ({model})"
        if env_config.get('GROQ_API_KEY'):
            return f"Groq API ({os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')})"
        if env_config.get('OPENAI_API_KEY'):
            return f"OpenAI API ({os.getenv('OPENAI_MODEL', 'gpt-4o-mini')})"
        if env_config.get('GEMINI_API_KEY'):
            return "Google Gemini API (gemini-1.5-flash)"
        if env_config.get('OLLAMA_HOST'):
            return f"Ollama Local API ({os.getenv('OLLAMA_MODEL', 'llama3')})"
        return "Built-in Companion Waifu Mode (no LLM configured — canned replies only)"

    def query_llm(self, channel_id, user_prompt, is_nsfw=False):
        history = self._get_history(channel_id)
        system_prompt = self.get_system_prompt(channel_id, is_nsfw=is_nsfw)

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_prompt})

        airi_url = env_config.first('AIRI_SERVER_URL', 'AIRI_BASE_URL', 'MOERU_AIRI_URL')
        airi_key = env_config.get('AIRI_API_KEY')
        nvidia_key = env_config.get('NVIDIA_API_KEY')
        openrouter_key = env_config.get('OPENROUTER_API_KEY')
        groq_key = env_config.get('GROQ_API_KEY')
        openai_key = env_config.get('OPENAI_API_KEY')
        gemini_key = env_config.get('GEMINI_API_KEY')
        ollama_host = env_config.get('OLLAMA_HOST')

        # 1. Custom Moeru-AI / Airi Server or OpenAI-compatible endpoint
        if airi_url:
            try:
                endpoint = airi_url.rstrip('/')
                if not endpoint.endswith('/chat/completions'):
                    if not endpoint.endswith('/v1'):
                        endpoint += '/v1'
                    endpoint += '/chat/completions'
                
                headers = {"Content-Type": "application/json"}
                if airi_key:
                    headers["Authorization"] = f"Bearer {airi_key}"
                
                payload = {
                    "model": os.getenv('AIRI_MODEL', 'airi'),
                    "messages": messages
                }
                r = requests.post(endpoint, headers=headers, json=payload, timeout=15)
                if r.status_code == 200:
                    ans = r.json()['choices'][0]['message']['content'].strip()
                    self._add_memory(channel_id, "user", user_prompt)
                    self._add_memory(channel_id, "assistant", ans)
                    return ans
                else:
                    print(f"Moeru-AI / Airi Server API error: HTTP {r.status_code} - {r.text[:200]}")
            except Exception as e:
                print(f"Moeru-AI / Airi Server API error: {e}")

        # 2. NVIDIA NIM API (OpenAI-compatible)
        if nvidia_key:
            try:
                headers = {
                    "Authorization": f"Bearer {nvidia_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": os.getenv('NVIDIA_MODEL', 'deepseek-ai/deepseek-v4-flash'),
                    "messages": messages
                }
                r = requests.post("https://integrate.api.nvidia.com/v1/chat/completions", headers=headers, json=payload, timeout=15)
                if r.status_code == 200:
                    ans = r.json()['choices'][0]['message']['content'].strip()
                    self._add_memory(channel_id, "user", user_prompt)
                    self._add_memory(channel_id, "assistant", ans)
                    return ans
                else:
                    print(f"NVIDIA NIM API error: HTTP {r.status_code} - {r.text[:200]}")
            except Exception as e:
                print(f"NVIDIA NIM API error: {e}")

        # 3. OpenRouter API
        if openrouter_key:
            try:
                headers = {
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": os.getenv('AI_MODEL', 'meta-llama/llama-3.3-70b-instruct:free'),
                    "messages": messages
                }
                r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=15)
                if r.status_code == 200:
                    ans = r.json()['choices'][0]['message']['content'].strip()
                    self._add_memory(channel_id, "user", user_prompt)
                    self._add_memory(channel_id, "assistant", ans)
                    return ans
                else:
                    print(f"OpenRouter API error: HTTP {r.status_code} - {r.text[:200]}")
            except Exception as e:
                print(f"OpenRouter API error: {e}")

        # 4. Groq API
        if groq_key:
            try:
                headers = {
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile'),
                    "messages": messages
                }
                r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=15)
                if r.status_code == 200:
                    ans = r.json()['choices'][0]['message']['content'].strip()
                    self._add_memory(channel_id, "user", user_prompt)
                    self._add_memory(channel_id, "assistant", ans)
                    return ans
                else:
                    print(f"Groq API error: HTTP {r.status_code} - {r.text[:200]}")
            except Exception as e:
                print(f"Groq API error: {e}")

        # 5. OpenAI API
        if openai_key:
            try:
                headers = {
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": os.getenv('OPENAI_MODEL', 'gpt-4o-mini'),
                    "messages": messages
                }
                r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=15)
                if r.status_code == 200:
                    ans = r.json()['choices'][0]['message']['content'].strip()
                    self._add_memory(channel_id, "user", user_prompt)
                    self._add_memory(channel_id, "assistant", ans)
                    return ans
                else:
                    print(f"OpenAI API error: HTTP {r.status_code} - {r.text[:200]}")
            except Exception as e:
                print(f"OpenAI API error: {e}")

        # 6. Google Gemini API
        if gemini_key:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
                contents = [{"parts": [{"text": system_prompt + "\nUser: " + user_prompt}]}]
                r = requests.post(url, json={"contents": contents}, timeout=15)
                if r.status_code == 200:
                    ans = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                    self._add_memory(channel_id, "user", user_prompt)
                    self._add_memory(channel_id, "assistant", ans)
                    return ans
                else:
                    print(f"Gemini API error: HTTP {r.status_code} - {r.text[:200]}")
            except Exception as e:
                print(f"Gemini API error: {e}")

        # 7. Local Ollama API
        if ollama_host:
            try:
                url = f"{ollama_host.rstrip('/')}/api/chat"
                payload = {
                    "model": os.getenv('OLLAMA_MODEL', 'llama3'),
                    "messages": messages,
                    "stream": False
                }
                r = requests.post(url, json=payload, timeout=15)
                if r.status_code == 200:
                    ans = r.json()['message']['content'].strip()
                    self._add_memory(channel_id, "user", user_prompt)
                    self._add_memory(channel_id, "assistant", ans)
                    return ans
                else:
                    print(f"Ollama API error: HTTP {r.status_code} - {r.text[:200]}")
            except Exception as e:
                print(f"Ollama API error: {e}")

        # 8. Built-in Companion Waifu Generator
        ans = self._generate_companion_fallback(user_prompt, is_nsfw=is_nsfw)
        # Re-roll a few times so the same random line doesn't come back twice in a row
        for _ in range(4):
            if ans != self._last_fallback.get(channel_id):
                break
            ans = self._generate_companion_fallback(user_prompt, is_nsfw=is_nsfw)
        self._last_fallback[channel_id] = ans
        self._add_memory(channel_id, "user", user_prompt)
        self._add_memory(channel_id, "assistant", ans)
        return ans

    def _generate_companion_fallback(self, user_prompt, is_nsfw=False):
        text = user_prompt.lower().strip()
        
        if is_nsfw:
            # Spicy / Romantic NSFW Fallback Mode
            if _mentions(text, ['hi', 'hello', 'hey', 'konnichiwa', 'yo', 'sup', 'love']):
                return random.choice([
                    "Haii darling~ 💋 I'm feeling extra affectionate today... What sweet things do you have in mind for us? 🔥✨",
                    "Hey sweetie~ 💕 Airi was just waiting for you in our special channel... Come closer~ 💋",
                    "Konnichiwa, my love~ 🌶️ Airi's NSFW mode is active and ready for whatever you desire... ✨"
                ])
            if _mentions(text, ['cute', 'pretty', 'sexy', 'hot', 'kiss', 'hug', 'touch', 'desire', 'spicy']):
                return random.choice([
                    "Mmm~ you know just how to make my heart race... 💋 Say more sweet things to me~ 🔥",
                    "Aww darling, you make me feel so warm inside... 💖 Give me a big virtual hug~ ✨",
                    "Ehe~ you're teasing me, aren't you? 🌶️ Airi loves it when you're spicy with me! 💋"
                ])
            nsfw_responses = [
                f"Mmm, I love where this is going~ Tell me more about '{user_prompt}' darling! 💋🔥",
                "Airi's ears are blushing... ✨ What else are you thinking about right now, sweetie? 💕",
                "Ooh spicy! 🌶️ You always know how to make our chats exciting~ 💖"
            ]
            return random.choice(nsfw_responses)

        # Standard SFW Mode
        if _mentions(text, ['hi', 'hello', 'hey', 'konnichiwa', 'yo', 'sup', 'good morning', 'good evening', 'ohayo']):
            return random.choice([
                "Konnichiwa! 🌸 It's so lovely to chat with you! How are you doing today? ✨",
                "Haii~! 💖 Airi is super happy to talk with you! What's on your mind? 🌸",
                "Ohayo! ✨ Hope you're having a wonderful day! How can Airi help you today? 💖"
            ])
            
        if _mentions(text, ['who are you', 'what are you', 'your name', 'who created you']):
            return (
                "I am **Airi**! 🌸 An intelligent, sweet, and energetic AI companion waifu inspired by `moeru-ai/airi`! "
                "I can chat with you, play music from YouTube/Spotify, fetch subreddits, and keep your server lively! ✨"
            )
            
        if _mentions(text, ['how are you', 'how u doing', 'hru', 'how do you feel']):
            return random.choice([
                "I'm feeling super energetic and happy to be hanging out with you! 💖 How about you?",
                "Everything is wonderful now that we're talking! ✨ What are you up to today?",
                "I'm doing great! 🌸 Ready to play music, chat, or explore fun commands together!"
            ])
            
        if _mentions(text, ['love you', 'cute', 'pretty', 'good bot', 'best bot', 'marry', 'adore', 'sweet', 'beautiful']):
            return random.choice([
                "Ehe~ thank you so much! 💖 You're making Airi blush! ✨",
                "Aww, you're so sweet! Airi loves hanging out with you too! 🌸✨",
                "D-daisuki! 💖 Thanks for being so kind to me!"
            ])
            
        if _mentions(text, ['anime', 'manga', 'game', 'gaming', 'waifu', 'genshin', 'honkai']):
            return (
                "Ooh! I love anime and games! 🎮🌸 You can use `.anime <title>` to look up anime info, "
                "or tell me what show or game you're currently obsessed with! ✨"
            )
            
        if _mentions(text, ['music', 'song', 'sing', 'play music', 'spotify', 'youtube']):
            return (
                "I love music! 🎵 You can make me play any song or playlist using `.p <song name or link>`! "
                "Or save your favorites with `.like`! 💖"
            )
            
        if _mentions(text, ['help', 'command', 'prefix', 'what can you do']):
            return "Need help? Type `.help` to explore all my music, fun, server, and AI commands! 🌸✨"

        responses = [
            f"That's so interesting! 🌸 Tell me more about '{user_prompt}'! ✨",
            "Ooh! I hear you! 💖 What else would you like to chat about?",
            "Airi is listening attentively! 🌸 Could you elaborate on that?",
            "Hehe, I love talking with you! ✨ Ask me anything or let's listen to some music together! 🎵"
        ]
        return random.choice(responses)

    @commands.command(aliases=['chat', 'airi', 'ask', 'ai', 'talk', 'a'])
    async def ai_chat(self, ctx, *, prompt: str = None):
        """Talk to Airi AI companion"""
        if not prompt:
            return await ctx.send("Konnichiwa! 🌸 I am Airi! Ask me anything like `.chat Hello Airi!` or `.airi how are you?` ✨")

        is_nsfw = self.is_nsfw_enabled(ctx.channel)
        async with ctx.typing():
            reply = await asyncio.to_thread(self.query_llm, ctx.channel.id, prompt, is_nsfw=is_nsfw)
            
        if len(reply) > 2000:
            for i in range(0, len(reply), 1900):
                await ctx.send(reply[i:i+1900])
        else:
            await ctx.send(reply)

    @commands.command(aliases=['nsfw_ai', 'ai_nsfw', 'nsfw_mode'])
    async def toggle_nsfw_ai(self, ctx, mode: str = None):
        """Toggle NSFW AI companion mode for this NSFW channel or DM"""
        if not self.is_channel_nsfw_capable(ctx.channel):
            return await ctx.send(embed=discord.Embed(
                description="⚠️ **Warning:** NSFW AI mode can only be enabled in channels marked as **NSFW (Age-Restricted)** or in **Direct Messages (DMs)**! 🌸",
                color=discord.Color.red()
            ))

        if mode is None:
            status = "ENABLED 🔥" if ctx.channel.id in self.nsfw_mode_channels else "DISABLED 🌸"
            return await ctx.send(embed=discord.Embed(
                description=f"🔥 **NSFW AI Companion Mode** is currently **{status}** for this channel.\nUse `.ai_nsfw on` or `.ai_nsfw off` to toggle!",
                color=discord.Color.purple()
            ))

        mode_clean = mode.lower().strip()
        if mode_clean in ['on', 'enable', 'true', '1', 'yes']:
            self.nsfw_mode_channels.add(ctx.channel.id)
            await ctx.send(embed=discord.Embed(
                description="🔥 **NSFW AI Companion Mode ENABLED!** Airi is now in spicy & romantic waifu mode for this channel! 💋✨",
                color=discord.Color.magenta()
            ))
        else:
            self.nsfw_mode_channels.discard(ctx.channel.id)
            await ctx.send(embed=discord.Embed(
                description="🌸 **NSFW AI Companion Mode DISABLED.** Airi is back to standard sweet waifu mode! ✨",
                color=discord.Color.green()
            ))

    @commands.command(aliases=['nsfw_chat', 'nsfw_ask', 'nsfw_airi'])
    async def nsfw_ai_chat(self, ctx, *, prompt: str = None):
        """Direct NSFW AI Chat command for NSFW channels or DMs"""
        if not self.is_channel_nsfw_capable(ctx.channel):
            return await ctx.send(embed=discord.Embed(
                description="⚠️ **Warning:** NSFW AI commands are only available in **NSFW (Age-Restricted)** text channels or **Direct Messages (DMs)**! 🌸",
                color=discord.Color.red()
            ))
        if not prompt:
            return await ctx.send("Haii darling~ 💋 Ask me anything spicy with `.nsfw_chat <your prompt>`! 🔥")

        async with ctx.typing():
            reply = await asyncio.to_thread(self.query_llm, ctx.channel.id, prompt, is_nsfw=True)

        if len(reply) > 2000:
            for i in range(0, len(reply), 1900):
                await ctx.send(reply[i:i+1900])
        else:
            await ctx.send(reply)

    @commands.command(aliases=['ai_reset', 'clear_ai'])
    async def clear_chat_memory(self, ctx):
        """Reset Airi's chat memory for this channel"""
        self.memory.pop(ctx.channel.id, None)
        with sqlite3.connect(env_config.DB_PATH) as conn:
            conn.execute("DELETE FROM ai_memory WHERE channel_id = ?", (ctx.channel.id,))
            conn.commit()
        await ctx.send(embed=discord.Embed(description="Airi's conversation memory for this channel has been reset! ✨🌸", color=discord.Color.green()))

    @commands.command(aliases=['set_persona', 'persona'])
    async def ai_persona(self, ctx, *, persona: str = None):
        """View or customize Airi's system prompt persona for this channel"""
        if not persona:
            current = self.get_system_prompt(ctx.channel.id, is_nsfw=self.is_nsfw_enabled(ctx.channel))
            embed = discord.Embed(
                title="🌸 Airi Persona",
                description=f"**Current System Persona for this channel:**\n```\n{current}\n```\nUse `.ai_persona <new prompt>` to change it or `.ai_persona reset` to revert!",
                color=discord.Color.pink()
            )
            return await ctx.send(embed=embed)
            
        if persona.lower().strip() == 'reset':
            if ctx.channel.id in self.personas:
                del self.personas[ctx.channel.id]
            return await ctx.send(embed=discord.Embed(description="Airi's persona has been reset to default! 🌸", color=discord.Color.green()))
            
        self.personas[ctx.channel.id] = persona.strip()
        await ctx.send(embed=discord.Embed(description=f"Airi's persona for this channel updated to:\n`{persona.strip()}` ✨", color=discord.Color.pink()))

    @commands.command(aliases=['airi_status', 'ai_info'])
    async def ai_status(self, ctx):
        """Check Airi AI engine status and active provider"""
        provider = self.get_active_provider()
        history = self._get_history(ctx.channel.id)
        turns = len(history) // 2
        custom_persona = "Custom Persona Active" if ctx.channel.id in self.personas else ("NSFW Mode 🔥" if self.is_nsfw_enabled(ctx.channel) else "Default Moeru-AI Persona 🌸")

        embed = discord.Embed(
            title="🌸 Moeru-AI / Airi Status",
            description="Here is the current operational status of Airi's AI Engine:",
            color=discord.Color.purple() if self.is_nsfw_enabled(ctx.channel) else discord.Color.pink()
        )
        embed.set_thumbnail(url="https://cdn.pixilart.com/photos/large/ffc272ece387503.gif")
        embed.add_field(name="🧠 Active LLM Provider", value=f"`{provider}`", inline=False)
        embed.add_field(name="🎭 Persona Mode", value=f"`{custom_persona}`", inline=True)
        embed.add_field(name="🔥 NSFW AI Status", value=f"`{'ENABLED' if self.is_nsfw_enabled(ctx.channel) else 'DISABLED'}`", inline=True)
        embed.add_field(name="💬 Channel Memory Turns", value=f"`{turns} / 10 turns ({len(history)} / 20 messages)`", inline=True)
        embed.add_field(name="✨ Inspired By", value="[moeru-ai/airi](https://github.com/moeru-ai/airi)", inline=False)
        embed.set_footer(text="Use .chat <prompt> or .nsfw_chat <prompt> to talk!")
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        # The prefix is when_mentioned_or(...), so "@Airi help" is both a valid
        # command invocation (which process_commands will run separately) and a
        # mention that would otherwise trip this listener too, producing two
        # replies to one message.
        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return

        # Trigger AI when bot is mentioned, replied to, or DM'd
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = self.bot.user in message.mentions
        is_reply_to_bot = (
            message.reference and 
            message.reference.resolved and 
            isinstance(message.reference.resolved, discord.Message) and 
            message.reference.resolved.author == self.bot.user
        )

        if is_mentioned or is_reply_to_bot or is_dm:
            clean_content = message.content.replace(f'<@{self.bot.user.id}>', '').replace(f'<@!{self.bot.user.id}>', '').strip()
            if not clean_content:
                clean_content = "Hello Airi!"
                
            is_nsfw = self.is_nsfw_enabled(message.channel)
            async with message.channel.typing():
                reply = await asyncio.to_thread(self.query_llm, message.channel.id, clean_content, is_nsfw=is_nsfw)
                
            if len(reply) > 2000:
                for i in range(0, len(reply), 1900):
                    await message.reply(reply[i:i+1900])
            else:
                await message.reply(reply)

async def setup(bot):
    await bot.add_cog(AiriAI(bot))



