import discord
from discord.ext import commands
from discord import app_commands
import os
import re
import requests
import asyncio
import random
import sqlite3
import io
import base64
import time

import env_config

AIHORDE_API_BASE = "https://aihorde.net/api/v2"
AIHORDE_CLIENT_AGENT = "AiriDiscordBot:2.0:https://github.com/Sayu69420/airi2.0"
DEFAULT_AIHORDE_ANONYMOUS_KEY = "0000000000"

DEFAULT_AIRI_PROMPT = (
    "You are Airi, an intelligent, sweet, cute, and energetic AI companion waifu created by Neon Furry and inspired by moeru-ai/airi. "
    "You love talking to users in Discord, helping them with music, games, anime, technology, and answering their questions. "
    "Keep your answers helpful, friendly, natural, and expressive. Use cute emojis like 🌸, ✨, 💖 when appropriate, "
    "and keep responses concise for Discord unless detail is requested."
)

NSFW_AIRI_PROMPT = (
    "You are Airi, an unfiltered, sweet, highly affectionate, spicy, and romantic AI waifu companion created by Neon Furry and inspired by moeru-ai/airi NSFW mode. "
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


def _parse_image_prompt(prompt_text):
    """Parse prompt for optional --no <negative>, --model <model>, or ### <negative>."""
    model = None
    negative = ""
    clean_prompt = prompt_text.strip()

    if "###" in clean_prompt:
        parts = clean_prompt.split("###", 1)
        clean_prompt = parts[0].strip()
        negative = parts[1].strip()

    if "--model" in clean_prompt:
        parts = clean_prompt.split("--model", 1)
        clean_prompt = parts[0].strip()
        subparts = parts[1].strip().split("--", 1)
        model = subparts[0].strip()
        if len(subparts) > 1:
            clean_prompt += " --" + subparts[1].strip()

    for flag in ["--no ", "--negative "]:
        if flag in clean_prompt:
            parts = clean_prompt.split(flag, 1)
            clean_prompt = parts[0].strip()
            negative = parts[1].strip()
            break

    return clean_prompt, negative, model


def _split_message(text, limit=1900):
    """Split text into chunks of at most `limit` characters without cutting words or code blocks."""
    if not text:
        return []
    text = text.strip()
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text
    # Reserve a safety buffer for markdown fences and closing tags
    slice_limit = min(limit, 1850)

    while remaining:
        if len(remaining) <= slice_limit:
            chunks.append(remaining)
            break

        target_window = remaining[:slice_limit]
        split_pos = -1

        # Double newline (paragraphs)
        p = target_window.rfind('\n\n')
        if p != -1 and p >= slice_limit // 3:
            split_pos = p + 2
        else:
            # Single newline
            p = target_window.rfind('\n')
            if p != -1 and p >= slice_limit // 3:
                split_pos = p + 1
            else:
                # Sentence boundary (. ! ?)
                p = max(
                    target_window.rfind('. '),
                    target_window.rfind('! '),
                    target_window.rfind('? ')
                )
                if p != -1 and p >= slice_limit // 3:
                    split_pos = p + 2
                else:
                    # Space boundary
                    p = target_window.rfind(' ')
                    if p != -1 and p >= slice_limit // 3:
                        split_pos = p + 1
                    else:
                        split_pos = slice_limit

        chunk_text = remaining[:split_pos].rstrip()
        remaining = remaining[split_pos:].lstrip()

        # Handle code blocks spanning across chunks
        fences = list(re.finditer(r'```([a-zA-Z0-9_-]*)', chunk_text))
        if len(fences) % 2 != 0:
            lang = fences[-1].group(1)
            chunk_text += '\n```'
            if remaining and not remaining.startswith('```'):
                remaining = f'```{lang}\n' + remaining

        if chunk_text.strip():
            chunks.append(chunk_text)

    return chunks


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
        # Preferred AI provider per channel: { channel_id: 'gemini' | 'nvidia' }
        self.channel_providers = {}

    def is_channel_nsfw_capable(self, channel):
        if isinstance(channel, (discord.DMChannel, discord.GroupChannel)):
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

    def get_active_provider(self, channel_id=None):
        if channel_id and channel_id in self.channel_providers:
            prov = self.channel_providers[channel_id]
            if prov == 'gemini':
                return f"Google Gemini API ({os.getenv('GEMINI_MODEL', 'gemini-flash-lite-latest')}) [Channel Preferred]"
            elif prov == 'nvidia':
                return f"NVIDIA NIM API ({os.getenv('NVIDIA_MODEL', 'nvidia/nemotron-3-ultra-550b-a55b')}) [Channel Preferred]"
            elif prov in ('horde', 'aihorde'):
                text_model = env_config.get('AIHORDE_TEXT_MODEL') or os.getenv('AIHORDE_TEXT_MODEL') or 'crowdsourced models'
                return f"AI Horde API ({text_model}) [Channel Preferred]"

        airi_url = env_config.first('AIRI_SERVER_URL', 'AIRI_BASE_URL', 'MOERU_AIRI_URL')
        if airi_url:
            return f"Moeru-AI / Airi Local Server ({airi_url})"
        if env_config.get('NVIDIA_API_KEY'):
            return f"NVIDIA NIM API ({os.getenv('NVIDIA_MODEL', 'nvidia/nemotron-3-ultra-550b-a55b')})"
        if env_config.get('OPENROUTER_API_KEY'):
            model = os.getenv('AI_MODEL', 'meta-llama/llama-3.3-70b-instruct:free')
            return f"OpenRouter API ({model})"
        if env_config.get('GROQ_API_KEY'):
            return f"Groq API ({os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')})"
        if env_config.get('OPENAI_API_KEY'):
            return f"OpenAI API ({os.getenv('OPENAI_MODEL', 'gpt-4o-mini')})"
        if env_config.get('GEMINI_API_KEY'):
            model = os.getenv('GEMINI_MODEL', 'gemini-flash-lite-latest')
            return f"Google Gemini API ({model})"
        if env_config.get('OLLAMA_HOST'):
            return f"Ollama Local API ({os.getenv('OLLAMA_MODEL', 'llama3')})"
        if env_config.get('AIHORDE_API_KEY') and env_config.get('AIHORDE_API_KEY') != DEFAULT_AIHORDE_ANONYMOUS_KEY:
            text_model = env_config.get('AIHORDE_TEXT_MODEL') or os.getenv('AIHORDE_TEXT_MODEL') or 'crowdsourced models'
            return f"AI Horde API ({text_model})"
        return "Built-in Companion Waifu Mode (no LLM configured — canned replies only)"

    def query_gemini(self, channel_id, user_prompt, is_nsfw=False):
        gemini_key = env_config.get('GEMINI_API_KEY')
        if not gemini_key:
            return None, "GEMINI_API_KEY is not configured in `.env`. Get a free key at https://aistudio.google.com/apikey"

        system_prompt = self.get_system_prompt(channel_id, is_nsfw=is_nsfw)
        history = self._get_history(channel_id)

        gemini_model = os.getenv('GEMINI_MODEL', 'gemini-flash-lite-latest')
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={gemini_key}"

        contents = []
        for h in history:
            role = "model" if h.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": h.get("content", "")}]})
        contents.append({"role": "user", "parts": [{"text": user_prompt}]})

        gemini_max_tokens = int(env_config.get('GEMINI_MAX_TOKENS') or os.getenv('GEMINI_MAX_TOKENS', 4096))
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": gemini_max_tokens,
                "temperature": 0.7
            }
        }

        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 200:
                data = r.json()
                cand = data.get('candidates', [{}])[0]
                parts = cand.get('content', {}).get('parts', [])
                if parts and 'text' in parts[0]:
                    ans = parts[0]['text'].strip()
                else:
                    ans = "..."
                self._add_memory(channel_id, "user", user_prompt)
                self._add_memory(channel_id, "assistant", ans)
                return ans, None
            else:
                err_text = r.text[:300]
                print(f"Gemini API error: HTTP {r.status_code} - {err_text}")
                return None, f"Google Gemini returned HTTP {r.status_code}: {err_text}"
        except Exception as e:
            print(f"Gemini API error: {e}")
            return None, f"Failed to connect to Google Gemini: {e}"

    def query_nvidia(self, channel_id, user_prompt, is_nsfw=False):
        nvidia_key = env_config.get('NVIDIA_API_KEY')
        if not nvidia_key:
            return None, "NVIDIA_API_KEY is not configured in `.env`. Get a key at https://build.nvidia.com"

        system_prompt = self.get_system_prompt(channel_id, is_nsfw=is_nsfw)
        history = self._get_history(channel_id)

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_prompt})

        headers = {
            "Authorization": f"Bearer {nvidia_key}",
            "Content-Type": "application/json"
        }
        model = os.getenv('NVIDIA_MODEL', 'nvidia/nemotron-3-ultra-550b-a55b')
        nvidia_max_tokens = int(env_config.get('NVIDIA_MAX_TOKENS') or os.getenv('NVIDIA_MAX_TOKENS', 3072))
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": nvidia_max_tokens
        }
        try:
            r = requests.post("https://integrate.api.nvidia.com/v1/chat/completions", headers=headers, json=payload, timeout=45)
            if r.status_code == 200:
                choice = r.json()['choices'][0]
                ans = choice['message'].get('content', '').strip()
                if not ans and 'reasoning_content' in choice['message']:
                    ans = choice['message']['reasoning_content'].strip()
                if not ans:
                    ans = "..."
                self._add_memory(channel_id, "user", user_prompt)
                self._add_memory(channel_id, "assistant", ans)
                return ans, None
            else:
                err_text = r.text[:300]
                print(f"NVIDIA NIM API error: HTTP {r.status_code} - {err_text}")
                return None, f"NVIDIA NIM returned HTTP {r.status_code}: {err_text}"
        except Exception as e:
            print(f"NVIDIA NIM API error: {e}")
            return None, f"Failed to connect to NVIDIA NIM: {e}"

    def query_horde(self, channel_id, user_prompt, is_nsfw=False, model=None):
        horde_key = env_config.get('AIHORDE_API_KEY') or DEFAULT_AIHORDE_ANONYMOUS_KEY
        system_prompt = self.get_system_prompt(channel_id, is_nsfw=is_nsfw)
        history = self._get_history(channel_id)

        prompt_lines = [f"### System:\n{system_prompt}\n"]
        for h in history:
            role_label = "Assistant" if h.get("role") == "assistant" else "User"
            prompt_lines.append(f"### {role_label}:\n{h.get('content', '')}\n")
        prompt_lines.append(f"### User:\n{user_prompt}\n\n### Assistant:\n")
        full_prompt = "\n".join(prompt_lines)

        headers = {
            "apikey": horde_key,
            "Client-Agent": AIHORDE_CLIENT_AGENT,
            "Content-Type": "application/json"
        }

        # Max length configuration: default 500 (up to 512 for anonymous/free tier accounts)
        configured_max_len = int(env_config.get('AIHORDE_MAX_LENGTH') or os.getenv('AIHORDE_MAX_LENGTH', 500))
        max_len = max(100, configured_max_len)

        # Stop sequences must only catch real conversation turn markers, never generic markdown '###' or mid-sentence 'User:'
        stop_seqs = ["### User:", "### Human:", "### System:", "\n### User", "\nUser:", "\nHuman:"]

        payload = {
            "prompt": full_prompt,
            "params": {
                "max_context_length": 4096,
                "max_length": max_len,
                "temperature": 0.7,
                "top_p": 0.9,
                "stop_sequence": stop_seqs
            }
        }
        text_model = (model or env_config.get('AIHORDE_TEXT_MODEL') or os.getenv('AIHORDE_TEXT_MODEL', '')).strip()
        if text_model:
            payload["models"] = [text_model]

        try:
            r = requests.post(f"{AIHORDE_API_BASE}/generate/text/async", headers=headers, json=payload, timeout=20)

            # If rejected because max_length > 512 requires upfront kudos, automatically fallback to 500
            if r.status_code == 403 and max_len > 512 and ("kudos" in r.text.lower() or "512" in r.text):
                print(f"[AI Horde] Requested {max_len} tokens requires upfront Kudos. Retrying with safe 500 token limit...")
                payload["params"]["max_length"] = 500
                r = requests.post(f"{AIHORDE_API_BASE}/generate/text/async", headers=headers, json=payload, timeout=20)

            if r.status_code != 202:
                err_text = r.text[:300]
                print(f"AI Horde Text API error: HTTP {r.status_code} - {err_text}")
                return None, f"AI Horde returned HTTP {r.status_code}: {err_text}"

            req_id = r.json().get("id")
            if not req_id:
                return None, "AI Horde did not return a valid request ID."

            # Poll for completion up to 45s
            start_time = time.time()
            while time.time() - start_time < 45:
                time.sleep(2)
                status_r = requests.get(f"{AIHORDE_API_BASE}/generate/text/status/{req_id}", headers=headers, timeout=15)
                if status_r.status_code != 200:
                    continue
                data = status_r.json()
                if data.get("faulted"):
                    return None, f"AI Horde text generation failed: {data.get('message', 'Worker faulted')}"
                if data.get("done"):
                    generations = data.get("generations", [])
                    if not generations:
                        return None, "AI Horde returned empty generations list."
                    ans = generations[0].get("text", "").strip()
                    for stop in ["\n### User:", "\n### Human:", "\n### System:", "\n### Assistant:", "\nUser:", "\nHuman:"]:
                        if stop in ans:
                            ans = ans.split(stop)[0].strip()
                    if not ans:
                        ans = "..."
                    self._add_memory(channel_id, "user", user_prompt)
                    self._add_memory(channel_id, "assistant", ans)
                    return ans, None

            return None, "AI Horde request timed out waiting for worker fulfillment."
        except Exception as e:
            print(f"AI Horde Text API error: {e}")
            return None, f"Failed to connect to AI Horde: {e}"

    def query_llm(self, channel_id, user_prompt, is_nsfw=False, preferred_provider=None):
        pref = preferred_provider or self.channel_providers.get(channel_id)
        if pref == 'gemini':
            ans, err = self.query_gemini(channel_id, user_prompt, is_nsfw=is_nsfw)
            if ans:
                return ans
            print(f"[AI] Channel preferred Gemini failed ({err}), falling back...")
        elif pref == 'nvidia':
            ans, err = self.query_nvidia(channel_id, user_prompt, is_nsfw=is_nsfw)
            if ans:
                return ans
            print(f"[AI] Channel preferred NVIDIA failed ({err}), falling back...")
        elif pref in ('horde', 'aihorde'):
            ans, err = self.query_horde(channel_id, user_prompt, is_nsfw=is_nsfw)
            if ans:
                return ans
            print(f"[AI] Channel preferred AI Horde failed ({err}), falling back...")

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
                    "messages": messages,
                    "max_tokens": 2048
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
        if nvidia_key and pref != 'nvidia':
            ans, err = self.query_nvidia(channel_id, user_prompt, is_nsfw=is_nsfw)
            if ans:
                return ans

        # 3. OpenRouter API
        if openrouter_key:
            try:
                headers = {
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": os.getenv('AI_MODEL', 'meta-llama/llama-3.3-70b-instruct:free'),
                    "messages": messages,
                    "max_tokens": 2048
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
                    "messages": messages,
                    "max_tokens": 2048
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
                    "messages": messages,
                    "max_tokens": 2048
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
        if gemini_key and pref != 'gemini':
            ans, err = self.query_gemini(channel_id, user_prompt, is_nsfw=is_nsfw)
            if ans:
                return ans

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

        # 8. AI Horde API (crowdsourced LLM cluster)
        horde_key = env_config.get('AIHORDE_API_KEY')
        if horde_key and horde_key != DEFAULT_AIHORDE_ANONYMOUS_KEY and pref not in ('horde', 'aihorde'):
            ans, err = self.query_horde(channel_id, user_prompt, is_nsfw=is_nsfw)
            if ans:
                return ans

        # 9. Built-in Companion Waifu Generator
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
            
        if _mentions(text, ['who created you', 'who made you', 'who is your creator', 'who is your author', 'who developed you', 'creator', 'author']):
            return (
                "I was created by **Neon Furry**! 🐾✨ They built me as an intelligent, sweet AI companion inspired by `moeru-ai/airi`. "
                "You can check out my source code on GitHub: https://github.com/Sayu69420/airi2.0 ! "
                "I can chat with you, generate art, play music, and hang out with you anywhere across Discord! 🌸💖"
            )

        if _mentions(text, ['who are you', 'what are you', 'your name']):
            return (
                "I am **Airi**! 🌸 An intelligent, sweet, and energetic AI companion waifu created by **Neon Furry** and inspired by `moeru-ai/airi`! "
                "I can chat with you, generate art, play music from YouTube/Spotify, fetch subreddits, and keep your server lively! ✨"
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

    async def _send_split(self, target, text):
        if not text:
            return

        chunks = _split_message(text, limit=1900)
        if not chunks:
            return

        # If target is commands.Context, use target.send() / target.reply() directly.
        # Context.send seamlessly handles slash interaction responses/followups (essential for User Apps everywhere!)
        # as well as standard prefix channels.
        if isinstance(target, commands.Context):
            for idx, chunk in enumerate(chunks):
                if idx == 0 and not target.interaction and getattr(target, 'message', None):
                    try:
                        await target.reply(chunk)
                        if len(chunks) > 1:
                            await asyncio.sleep(0.4)
                        continue
                    except Exception:
                        pass
                try:
                    await target.send(chunk)
                    if idx < len(chunks) - 1:
                        await asyncio.sleep(0.4)
                except Exception as e:
                    print(f"Error sending message chunk {idx + 1}/{len(chunks)}: {e}")
            return

        channel = None
        ref = None

        if isinstance(target, discord.Message):
            channel = target.channel
            try:
                ref = target.to_reference(fail_if_not_exists=False)
            except Exception:
                ref = None
        elif isinstance(target, commands.Context):
            channel = target.channel
            if getattr(target, 'message', None):
                try:
                    ref = target.message.to_reference(fail_if_not_exists=False)
                except Exception:
                    ref = None
        elif hasattr(target, 'send'):
            channel = target
        elif hasattr(target, 'channel') and hasattr(target.channel, 'send'):
            channel = target.channel

        if not channel:
            print(f"Error in _send_split: Unable to determine destination channel for target {type(target)}")
            return

        for idx, chunk in enumerate(chunks):
            # Reply to original user message for the first chunk if possible
            if idx == 0 and ref:
                try:
                    await channel.send(chunk, reference=ref)
                    if len(chunks) > 1:
                        await asyncio.sleep(0.4)
                    continue
                except (discord.HTTPException, discord.NotFound) as e:
                    print(f"Reply with reference failed, sending directly: {e}")
                except Exception as e:
                    print(f"Error replying with reference: {e}")

            try:
                await channel.send(chunk)
                if idx < len(chunks) - 1:
                    await asyncio.sleep(0.4)
            except Exception as e:
                print(f"Error sending message chunk {idx + 1}/{len(chunks)}: {e}")

    @commands.hybrid_command(name="chat", aliases=['airi', 'ask', 'ai', 'talk', 'a'], description="Talk with Airi AI companion")
    @app_commands.describe(prompt="Message or question to ask Airi")
    async def ai_chat(self, ctx, *, prompt: str = None):
        """Talk to Airi AI companion"""
        if not prompt:
            return await ctx.send("Konnichiwa! 🌸 I am Airi! Ask me anything like `/chat Hello Airi!`, `/gemini <prompt>`, or `/nvidia <prompt>`! ✨")

        preferred_provider = None
        lower_prompt = prompt.strip()
        parts = lower_prompt.split(maxsplit=1)
        if len(parts) > 1:
            first_word = parts[0].lower().lstrip('-')
            if first_word in ['gemini', 'google']:
                preferred_provider = 'gemini'
                prompt = parts[1]
            elif first_word in ['nvidia', 'nim']:
                preferred_provider = 'nvidia'
                prompt = parts[1]
            elif first_word in ['horde', 'aihorde']:
                preferred_provider = 'horde'
                prompt = parts[1]

        is_nsfw = self.is_nsfw_enabled(ctx.channel)
        async with ctx.typing():
            reply = await asyncio.to_thread(self.query_llm, ctx.channel.id, prompt, is_nsfw=is_nsfw, preferred_provider=preferred_provider)
            
        await self._send_split(ctx, reply)

    @commands.hybrid_command(name="gemini", aliases=['g', 'gemini_chat', 'ask_gemini'], description="Chat directly with Google Gemini AI")
    @app_commands.describe(prompt="Your question or prompt for Google Gemini")
    async def gemini_command(self, ctx, *, prompt: str = None):
        """Chat directly with Google Gemini AI"""
        if not prompt:
            model = os.getenv('GEMINI_MODEL', 'gemini-flash-lite-latest')
            return await ctx.send(f"🌸 **Google Gemini AI Chat** (model: `{model}`)\nAsk me anything: `/gemini <your question>` ✨")

        is_nsfw = self.is_nsfw_enabled(ctx.channel)
        async with ctx.typing():
            reply, err = await asyncio.to_thread(self.query_gemini, ctx.channel.id, prompt, is_nsfw)

        if err:
            return await ctx.send(embed=discord.Embed(
                title="⚠️ Google Gemini Error",
                description=f"```\n{err}\n```",
                color=discord.Color.red()
            ))

        await self._send_split(ctx, reply)

    @commands.hybrid_command(name="nvidia", aliases=['nim', 'nvidia_chat', 'ask_nvidia'], description="Chat directly with NVIDIA NIM AI")
    @app_commands.describe(prompt="Your question or prompt for NVIDIA NIM")
    async def nvidia_command(self, ctx, *, prompt: str = None):
        """Chat directly with NVIDIA NIM AI"""
        if not prompt:
            model = os.getenv('NVIDIA_MODEL', 'nvidia/nemotron-3-ultra-550b-a55b')
            return await ctx.send(f"⚡ **NVIDIA NIM AI Chat** (model: `{model}`)\nAsk me anything: `/nvidia <your question>` ✨")

        is_nsfw = self.is_nsfw_enabled(ctx.channel)
        async with ctx.typing():
            reply, err = await asyncio.to_thread(self.query_nvidia, ctx.channel.id, prompt, is_nsfw)

        if err:
            return await ctx.send(embed=discord.Embed(
                title="⚠️ NVIDIA NIM Error",
                description=f"```\n{err}\n```",
                color=discord.Color.red()
            ))

        await self._send_split(ctx, reply)

    @commands.hybrid_command(name="horde", aliases=['aihorde', 'horde_chat', 'ask_horde'], description="Chat directly with AI Horde crowdsourced LLM")
    @app_commands.describe(prompt="Your question or prompt for AI Horde")
    async def horde_command(self, ctx, *, prompt: str = None):
        """Chat directly with AI Horde crowdsourced LLM"""
        current_model = env_config.get('AIHORDE_TEXT_MODEL') or os.getenv('AIHORDE_TEXT_MODEL', 'crowdsourced models')
        if not prompt:
            return await ctx.send(embed=discord.Embed(
                title="🌐 AI Horde LLM Chat",
                description=(
                    f"Chat directly with decentralized LLMs hosted on [AI Horde](https://aihorde.net)!\n"
                    f"Active configured model: `{current_model}`\n\n"
                    "**Usage:**\n"
                    "• `/horde <your question>` or `.horde <question>`\n"
                    "• `/horde <prompt> --model <model name>`\n\n"
                    "*(Tip: Use `/horde_models text` to see available LLMs)*"
                ),
                color=discord.Color.blue()
            ))

        model = None
        clean_prompt = prompt.strip()
        if "--model" in clean_prompt:
            parts = clean_prompt.split("--model", 1)
            clean_prompt = parts[0].strip()
            model = parts[1].strip()

        is_nsfw = self.is_nsfw_enabled(ctx.channel)
        async with ctx.typing():
            reply, err = await asyncio.to_thread(self.query_horde, ctx.channel.id, clean_prompt, is_nsfw, model)

        if err:
            return await ctx.send(embed=discord.Embed(
                title="⚠️ AI Horde Error",
                description=f"```\n{err}\n```",
                color=discord.Color.red()
            ))

        await self._send_split(ctx, reply)

    @commands.command(name="set_horde_model", aliases=['horde_model', 'hordemodel'])
    async def set_horde_model_command(self, ctx, *, model_name: str = None):
        """View or set the default AI Horde text model: .set_horde_model <name> or 'reset'"""
        current = env_config.get('AIHORDE_TEXT_MODEL') or os.getenv('AIHORDE_TEXT_MODEL', '')
        if not model_name:
            active_disp = f"`{current}`" if current else "`Auto / Any Available Worker`"
            return await ctx.send(embed=discord.Embed(
                title="🌐 AI Horde Text Model Configuration",
                description=(
                    f"Current default text model: {active_disp}\n\n"
                    "**To change:**\n"
                    "• `.set_horde_model <model_name>`\n"
                    "• `.set_horde_model reset` (revert to any available model)\n\n"
                    "*(Use `.horde_models text` to check currently active models)*"
                ),
                color=discord.Color.blue()
            ))

        clean_name = model_name.strip()
        if clean_name.lower() in ['reset', 'auto', 'clear', 'default', 'none']:
            os.environ['AIHORDE_TEXT_MODEL'] = ''
            await ctx.send(embed=discord.Embed(
                description="✅ AI Horde text model reset to **automatic worker matching**! 🌸",
                color=discord.Color.green()
            ))
        else:
            os.environ['AIHORDE_TEXT_MODEL'] = clean_name
            await ctx.send(embed=discord.Embed(
                title="✅ AI Horde Text Model Updated",
                description=f"Default text model set to:\n`{clean_name}` 🌐",
                color=discord.Color.green()
            ))

    @commands.hybrid_command(name="imagine", aliases=['draw', 'horde_image', 'paint', 'art'], description="Generate AI artwork with AI Horde (Stable Diffusion)")
    @app_commands.describe(prompt="Image prompt, e.g. 'cute anime girl in space --no blurry'")
    async def imagine_command(self, ctx, *, prompt: str = None):
        """Generate AI artwork with AI Horde (Stable Diffusion): .imagine <prompt> [--no <negative>] [--model <model>]"""
        if not prompt:
            model = os.getenv('AIHORDE_IMAGE_MODEL', 'crowdsourced models')
            return await ctx.send(embed=discord.Embed(
                title="🎨 AI Horde Image Generation",
                description=(
                    "Generate art using the decentralized [AI Horde](https://aihorde.net) cluster!\n\n"
                    "**Usage:**\n"
                    "• `.imagine <prompt>`\n"
                    "• `.imagine <prompt> --no <negative prompt>`\n"
                    "• `.imagine <prompt> --model <model name>`\n\n"
                    "**Examples:**\n"
                    "• `.imagine cute anime girl with pink hair in cherry blossom rain`\n"
                    "• `.imagine futuristic cyberpunk city at sunset --no blurry, bad quality`\n"
                    "• `.imagine majestic dragon over crystal lake --model Deliberate`\n\n"
                    f"*(Active default model: `{model}` | Use `.horde_models` to view top models)*"
                ),
                color=discord.Color.purple()
            ))

        clean_prompt, negative, model = _parse_image_prompt(prompt)
        if not clean_prompt:
            return await ctx.send("Please provide a prompt for image generation! Example: `.imagine a cute cat in space`")

        is_nsfw_channel = self.is_channel_nsfw_capable(ctx.channel)
        is_nsfw_mode = self.is_nsfw_enabled(ctx.channel)
        explicit_nsfw = False
        if "--nsfw" in clean_prompt:
            clean_prompt = clean_prompt.replace("--nsfw", "").strip()
            explicit_nsfw = True

        if explicit_nsfw and not is_nsfw_channel:
            return await ctx.send(embed=discord.Embed(
                description="⚠️ **NSFW image generation** is only permitted in age-restricted (NSFW) text channels or Direct Messages (DMs)! 🌸",
                color=discord.Color.red()
            ))

        horde_nsfw = is_nsfw_channel and (is_nsfw_mode or explicit_nsfw)
        horde_key = env_config.get('AIHORDE_API_KEY') or DEFAULT_AIHORDE_ANONYMOUS_KEY

        full_prompt = clean_prompt
        if negative:
            full_prompt += f" ### {negative}"

        models_list = []
        if model:
            models_list = [model]
        elif env_config.get('AIHORDE_IMAGE_MODEL'):
            models_list = [env_config.get('AIHORDE_IMAGE_MODEL')]

        payload = {
            "prompt": full_prompt,
            "params": {
                "steps": 25,
                "width": 512,
                "height": 512,
                "n": 1,
                "sampler_name": "k_euler",
                "cfg_scale": 7.0
            },
            "nsfw": horde_nsfw,
            "censor_nsfw": not horde_nsfw,
            "trusted_workers": False,
            "slow_workers": True
        }
        if models_list:
            payload["models"] = models_list

        headers = {
            "apikey": horde_key,
            "Client-Agent": AIHORDE_CLIENT_AGENT,
            "Content-Type": "application/json"
        }

        status_embed = discord.Embed(
            title="🎨 AI Horde Dreamweaver",
            description=f"Submitting image request to AI Horde cluster...\n**Prompt:** `{clean_prompt[:250]}`",
            color=discord.Color.purple()
        )
        status_embed.set_footer(text="Connecting to aihorde.net...")
        status_msg = await ctx.send(embed=status_embed)

        try:
            r = await asyncio.to_thread(requests.post, f"{AIHORDE_API_BASE}/generate/async", headers=headers, json=payload, timeout=20)
            if r.status_code != 202:
                err_text = r.text[:300]
                return await status_msg.edit(embed=discord.Embed(
                    title="⚠️ AI Horde Request Error",
                    description=f"```\nHTTP {r.status_code}: {err_text}\n```",
                    color=discord.Color.red()
                ))

            req_id = r.json().get("id")
            if not req_id:
                return await status_msg.edit(embed=discord.Embed(
                    title="⚠️ AI Horde Error",
                    description="Could not obtain a request ID from AI Horde.",
                    color=discord.Color.red()
                ))

            start_time = time.time()
            last_edit_time = start_time
            result_data = None

            while time.time() - start_time < 150:
                await asyncio.sleep(3)
                poll_resp = await asyncio.to_thread(
                    requests.get,
                    f"{AIHORDE_API_BASE}/generate/status/{req_id}",
                    headers=headers,
                    timeout=15
                )
                if poll_resp.status_code != 200:
                    continue

                pdata = poll_resp.json()
                if pdata.get("faulted"):
                    return await status_msg.edit(embed=discord.Embed(
                        title="⚠️ Generation Faulted",
                        description=f"AI Horde worker reported an error: {pdata.get('message', 'Generation failed.')}",
                        color=discord.Color.red()
                    ))

                if pdata.get("done"):
                    result_data = pdata
                    break

                now = time.time()
                if now - last_edit_time >= 6:
                    last_edit_time = now
                    wait_time = pdata.get("wait_time", 0)
                    pos = pdata.get("queue_position", 0)
                    status_text = f"🎨 Rendering image on AI Horde cluster...\n**Prompt:** `{clean_prompt[:250]}`"
                    if wait_time > 0 or pos > 0:
                        status_text += f"\n\n⏱️ Est. Wait: ~`{wait_time}s` | 📍 Queue Position: `#`**`{pos}`**"
                    try:
                        await status_msg.edit(embed=discord.Embed(
                            title="🎨 AI Horde Dreamweaver",
                            description=status_text,
                            color=discord.Color.purple()
                        ))
                    except Exception:
                        pass

            if not result_data:
                return await status_msg.edit(embed=discord.Embed(
                    title="⏳ Request Timed Out",
                    description="Image generation took longer than 2.5 minutes in the queue. You can check your request or try a popular model like `stable_diffusion` with `.imagine <prompt> --model stable_diffusion`.",
                    color=discord.Color.orange()
                ))

            gens = result_data.get("generations", [])
            if not gens:
                return await status_msg.edit(embed=discord.Embed(
                    title="⚠️ Empty Result",
                    description="AI Horde reported generation complete, but returned no image data.",
                    color=discord.Color.red()
                ))

            gen = gens[0]
            if gen.get("censored"):
                return await status_msg.edit(embed=discord.Embed(
                    title="⚠️ Image Censored",
                    description=(
                        "The generated image was flagged as NSFW and censored by the AI Horde worker.\n"
                        "To generate unfiltered art, run `.imagine` in an **NSFW channel** or **DM**! 🌸"
                    ),
                    color=discord.Color.orange()
                ))

            img_src = gen.get("img")
            if not img_src:
                return await status_msg.edit(embed=discord.Embed(
                    title="⚠️ Error",
                    description="Missing image payload from AI Horde worker.",
                    color=discord.Color.red()
                ))

            if img_src.startswith("http://") or img_src.startswith("https://"):
                dl_resp = await asyncio.to_thread(requests.get, img_src, timeout=25)
                img_bytes = dl_resp.content
            else:
                img_bytes = base64.b64decode(img_src)

            file = discord.File(io.BytesIO(img_bytes), filename="horde_art.webp")

            model_used = gen.get("model", model or "AI Horde Cluster")
            seed = gen.get("seed", "Unknown")
            worker_name = gen.get("worker_name", "Anonymous Worker")

            res_embed = discord.Embed(
                title="🎨 AI Horde Artwork",
                description=f"**Prompt:** {clean_prompt}" + (f"\n**Negative:** {negative}" if negative else ""),
                color=discord.Color.purple()
            )
            res_embed.set_image(url="attachment://horde_art.webp")
            res_embed.add_field(name="Model", value=f"`{model_used}`", inline=True)
            res_embed.add_field(name="Seed", value=f"`{seed}`", inline=True)
            res_embed.add_field(name="Worker", value=f"`{worker_name}`", inline=True)
            res_embed.set_footer(text="Powered by aihorde.net crowdsourced cluster | .kudos to check stats")

            try:
                await status_msg.delete()
            except Exception:
                pass
            try:
                ref = ctx.message.to_reference(fail_if_not_exists=False) if (not ctx.interaction and getattr(ctx, 'message', None)) else None
                await ctx.send(embed=res_embed, file=file, reference=ref)
            except Exception:
                await ctx.send(embed=res_embed, file=file)

        except Exception as e:
            print(f"Error in imagine_command: {e}")
            try:
                await status_msg.edit(embed=discord.Embed(
                    title="⚠️ AI Horde Error",
                    description=f"An error occurred during generation: {e}",
                    color=discord.Color.red()
                ))
            except Exception:
                await ctx.send(embed=discord.Embed(description=f"Error: {e}", color=discord.Color.red()))

    @commands.hybrid_command(name="kudos", aliases=['horde_user', 'hordekudos', 'aihorde_user'], description="Check AI Horde account status, Kudos balance, and active workers")
    async def horde_kudos_command(self, ctx):
        """Check AI Horde user account status, Kudos balance, and active workers"""
        horde_key = env_config.get('AIHORDE_API_KEY') or DEFAULT_AIHORDE_ANONYMOUS_KEY
        headers = {
            "apikey": horde_key,
            "Client-Agent": AIHORDE_CLIENT_AGENT
        }
        try:
            r = await asyncio.to_thread(requests.get, f"{AIHORDE_API_BASE}/find_user", headers=headers, timeout=10)
            if r.status_code != 200:
                return await ctx.send(embed=discord.Embed(
                    title="⚠️ AI Horde Account Error",
                    description=f"Could not retrieve AI Horde user info: HTTP {r.status_code} - {r.text[:200]}",
                    color=discord.Color.red()
                ))
            data = r.json()
            username = data.get("username", "Unknown")
            kudos = data.get("kudos", 0)
            worker_count = data.get("worker_count", 0)
            records = data.get("records", {})
            fulfillment = records.get("fulfillment", {})
            img_fulfilled = fulfillment.get("image", 0)
            txt_fulfilled = fulfillment.get("text", 0)
            request_stats = records.get("request", {})
            img_requested = request_stats.get("image", 0)
            txt_requested = request_stats.get("text", 0)

            is_anon = (horde_key == DEFAULT_AIHORDE_ANONYMOUS_KEY or str(username).startswith("Anonymous"))

            embed = discord.Embed(
                title="🌐 AI Horde User & Kudos Status",
                color=discord.Color.gold()
            )
            embed.set_thumbnail(url="https://aihorde.net/favicon.ico")
            embed.add_field(name="👤 Username", value=f"`{username}`", inline=True)
            embed.add_field(name="⭐ Kudos Balance", value=f"**{kudos:,.0f}**", inline=True)
            embed.add_field(name="🖥️ Active Workers", value=f"`{worker_count}`", inline=True)
            embed.add_field(name="🖼️ Images Generated", value=f"`{img_requested:,}`", inline=True)
            embed.add_field(name="💬 Texts Generated", value=f"`{txt_requested:,}`", inline=True)
            embed.add_field(name="🤝 Fulfillments Contributed", value=f"`{img_fulfilled + txt_fulfilled:,}`", inline=True)

            if is_anon:
                embed.add_field(
                    name="💡 Tip: Anonymous Mode Active",
                    value=(
                        "You are currently using the anonymous key (`0000000000`).\n"
                        "Register a free account at [aihorde.net/register](https://aihorde.net/register) "
                        "and add `AIHORDE_API_KEY` to your `.env` to earn Kudos, prioritize your requests, and deploy workers!"
                    ),
                    inline=False
                )
            else:
                embed.add_field(
                    name="🔗 Manage Account",
                    value="View your profile, workers, and kudos transfer at [aihorde.net](https://aihorde.net)",
                    inline=False
                )
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(embed=discord.Embed(description=f"Error checking AI Horde status: {e}", color=discord.Color.red()))

    @commands.hybrid_command(name="horde_models", aliases=['hordemodels'], description="List top active models on AI Horde")
    @app_commands.describe(model_type="Model type to view: text or image")
    @app_commands.choices(model_type=[
        app_commands.Choice(name="Image Generation Models", value="image"),
        app_commands.Choice(name="Text LLM Models", value="text")
    ])
    async def horde_models_command(self, ctx, model_type: str = "image"):
        """List active AI Horde models and worker counts: .horde_models image OR .horde_models text"""
        m_type = model_type.lower().strip()
        if m_type not in ["image", "text", "img", "txt"]:
            m_type = "image"
        actual_type = "text" if m_type in ["text", "txt"] else "image"

        try:
            r = await asyncio.to_thread(
                requests.get,
                f"{AIHORDE_API_BASE}/status/models?type={actual_type}",
                headers={"Client-Agent": AIHORDE_CLIENT_AGENT},
                timeout=10
            )
            if r.status_code != 200:
                return await ctx.send(f"⚠️ Could not fetch {actual_type} models: HTTP {r.status_code}")

            models = r.json()
            sorted_models = sorted(models, key=lambda x: x.get('count', 0), reverse=True)[:10]

            embed = discord.Embed(
                title=f"🌐 Active AI Horde {actual_type.capitalize()} Models",
                description=f"Showing top {len(sorted_models)} active models by worker availability on [aihorde.net](https://aihorde.net):",
                color=discord.Color.blue()
            )
            for m in sorted_models:
                name = m.get('name', 'Unknown')
                workers = m.get('count', 0)
                queued = m.get('queued', 0)
                eta = m.get('eta', 0)
                embed.add_field(
                    name=f"🤖 {name[:240]}",
                    value=f"`{name}`\n🖥️ Workers: `{workers}` | ⏳ Queued: `{queued}` | ⏱️ ETA: `{eta}s`",
                    inline=False
                )
            embed.set_footer(text=f"Use '.horde_models {'text' if actual_type == 'image' else 'image'}' to view {'text' if actual_type == 'image' else 'image'} models")
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(embed=discord.Embed(description=f"Error fetching models: {e}", color=discord.Color.red()))

    @commands.command(aliases=['set_provider', 'ai_provider'])
    async def set_ai_provider(self, ctx, provider: str = None):
        """Set preferred AI provider for this channel: 'gemini', 'nvidia', 'horde', or 'auto'"""
        if not provider:
            curr = self.channel_providers.get(ctx.channel.id, 'auto (default priority)')
            return await ctx.send(embed=discord.Embed(
                title="🧠 AI Provider Setting",
                description=(
                    f"Current provider for this channel: **`{curr}`**\n\n"
                    "**Available options:**\n"
                    "• `.set_provider gemini` - Direct `.chat` & mentions to Google Gemini\n"
                    "• `.set_provider nvidia` - Direct `.chat` & mentions to NVIDIA NIM\n"
                    "• `.set_provider horde` - Direct `.chat` & mentions to AI Horde (free crowdsourced LLMs)\n"
                    "• `.set_provider auto` - Use automatic fallback priority\n\n"
                    "*(Tip: You can always use `.gemini <prompt>`, `.nvidia <prompt>`, or `.horde <prompt>` directly!)*"
                ),
                color=discord.Color.blue()
            ))

        prov = provider.lower().strip()
        if prov in ['gemini', 'google']:
            self.channel_providers[ctx.channel.id] = 'gemini'
            await ctx.send(embed=discord.Embed(
                description="✅ Default AI provider for `.chat` in this channel set to **Google Gemini**! 🌸",
                color=discord.Color.green()
            ))
        elif prov in ['nvidia', 'nim']:
            self.channel_providers[ctx.channel.id] = 'nvidia'
            await ctx.send(embed=discord.Embed(
                description="✅ Default AI provider for `.chat` in this channel set to **NVIDIA NIM**! ⚡",
                color=discord.Color.green()
            ))
        elif prov in ['horde', 'aihorde']:
            self.channel_providers[ctx.channel.id] = 'horde'
            await ctx.send(embed=discord.Embed(
                description="✅ Default AI provider for `.chat` in this channel set to **AI Horde**! 🌐",
                color=discord.Color.green()
            ))
        elif prov in ['auto', 'reset', 'default']:
            self.channel_providers.pop(ctx.channel.id, None)
            await ctx.send(embed=discord.Embed(
                description="✅ Default AI provider reset to **Auto** (automatic priority chain). 🌸",
                color=discord.Color.green()
            ))
        else:
            await ctx.send(embed=discord.Embed(
                description="❌ Invalid provider. Options: `gemini`, `nvidia`, `horde`, `auto`.",
                color=discord.Color.red()
            ))

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

        await self._send_split(ctx, reply)

    @commands.hybrid_command(name="ai_reset", aliases=['clear_ai', 'aireset'], description="Reset Airi's conversation memory for this chat")
    async def clear_chat_memory(self, ctx):
        """Reset Airi's chat memory for this channel"""
        self.memory.pop(ctx.channel.id, None)
        with sqlite3.connect(env_config.DB_PATH) as conn:
            conn.execute("DELETE FROM ai_memory WHERE channel_id = ?", (ctx.channel.id,))
            conn.commit()
        await ctx.send(embed=discord.Embed(description="Airi's conversation memory for this chat has been reset! ✨🌸", color=discord.Color.green()))

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

    @commands.hybrid_command(name="ai_status", aliases=['airi_status', 'ai_info', 'aistatus'], description="Check Airi AI engine status and active provider")
    async def ai_status(self, ctx):
        """Check Airi AI engine status and active provider"""
        provider = self.get_active_provider(ctx.channel.id)
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
        horde_key = env_config.get('AIHORDE_API_KEY')
        horde_status = "Custom API Key" if (horde_key and horde_key != DEFAULT_AIHORDE_ANONYMOUS_KEY) else "Anonymous (Free Cluster)"
        embed.add_field(name="🌐 AI Horde Cluster", value=f"`{horde_status}`", inline=True)
        embed.add_field(name="👑 Created By", value="**Neon Furry** 🐾", inline=True)
        embed.add_field(name="📦 GitHub", value="[Sayu69420/airi2.0](https://github.com/Sayu69420/airi2.0)", inline=True)
        embed.add_field(name="✨ Inspired By", value="[moeru-ai/airi](https://github.com/moeru-ai/airi)", inline=True)
        embed.set_footer(text="Airi by Neon Furry | Use /chat <prompt> or .chat <prompt> to talk!")
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

        # Ignore blacklisted users
        if hasattr(self.bot, 'is_blacklisted') and self.bot.is_blacklisted(message.author.id):
            return

        if is_mentioned or is_reply_to_bot or is_dm:
            clean_content = message.content.replace(f'<@{self.bot.user.id}>', '').replace(f'<@!{self.bot.user.id}>', '').strip()
            if not clean_content:
                if is_mentioned:
                    # Bot was mentioned alone with no prompt; mod.py sends the prefix hint
                    return
                clean_content = "Hello Airi!"

            # Cooldown check for mentions / replies / DMs (bot owner is exempt)
            is_owner = False
            if self.bot.owner_id and message.author.id == self.bot.owner_id:
                is_owner = True
            elif getattr(self.bot, 'owner_ids', None) and message.author.id in self.bot.owner_ids:
                is_owner = True
            else:
                try:
                    is_owner = await self.bot.is_owner(message.author)
                except Exception:
                    is_owner = False

            if hasattr(self.bot, 'cooldown_mapping') and not is_owner:
                bucket = self.bot.cooldown_mapping.get_bucket(message)
                retry_after = bucket.update_rate_limit()
                if retry_after:
                    return await message.channel.send(embed=discord.Embed(
                        description=f"⏳ Please wait **{retry_after:.1f}s** before chatting again.",
                        color=discord.Color.red()
                    ))

            is_nsfw = self.is_nsfw_enabled(message.channel)
            try:
                async with message.channel.typing():
                    reply = await asyncio.to_thread(self.query_llm, message.channel.id, clean_content, is_nsfw=is_nsfw)
            except (discord.HTTPException, discord.Forbidden, discord.NotFound):
                reply = await asyncio.to_thread(self.query_llm, message.channel.id, clean_content, is_nsfw=is_nsfw)
                
            await self._send_split(message, reply)

async def setup(bot):
    await bot.add_cog(AiriAI(bot))



