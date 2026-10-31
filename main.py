import discord, datetime, os
import sqlite3
import traceback
from discord.ext import commands
from discord import app_commands

import env_config

def get_prefix(bot, msg):
    if not msg.guild:
        return commands.when_mentioned_or(".")(bot, msg)
    try:
        with sqlite3.connect(env_config.DB_PATH) as db:
            cursor = db.cursor()
            cursor.execute("SELECT prefix FROM config WHERE guild_id = ?", (msg.guild.id,))
            row = cursor.fetchone()
            if row and row[0]:
                return commands.when_mentioned_or(row[0], ".")(bot, msg)
    except Exception as e:
        print(f"Database error in get_prefix: {e}")
    return commands.when_mentioned_or(".")(bot, msg)
    
intents = discord.Intents.default()
intents.message_content = True

class UserBlacklisted(commands.CheckFailure):
    """Raised when a blacklisted user attempts to invoke a command."""
    pass

class AiriCommandTree(app_commands.CommandTree):
    """Command tree that automatically enables user installation and all contexts.

    This allows Discord members to install Airi to their personal profiles
    and execute slash commands everywhere across Discord (servers, DMs, group chats).
    """
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Global check for all slash command interactions."""
        user_id = interaction.user.id
        if hasattr(self.client, 'is_blacklisted') and self.client.is_blacklisted(user_id):
            embed = discord.Embed(
                title="⛔ Access Denied",
                description="You are **blacklisted** from using Airi commands.",
                color=discord.Color.red()
            )
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception as e:
                print(f"Error sending blacklist interaction response: {e}")
            return False
        return True

    async def sync(self, *, guild=None):
        for cmd in self.walk_commands():
            if getattr(cmd, 'parent', None) is None:
                if getattr(cmd, 'allowed_installs', None) is None:
                    cmd.allowed_installs = app_commands.AppInstallationType(guild=True, user=True)
                if getattr(cmd, 'allowed_contexts', None) is None:
                    cmd.allowed_contexts = app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True)
        return await super().sync(guild=guild)

class AiriBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('tree_cls', AiriCommandTree)
        super().__init__(*args, **kwargs)
        cooldown_sec = float(env_config.get('COMMAND_COOLDOWN_SECONDS', 10.0) or 10.0)
        self.cooldown_mapping = commands.CooldownMapping.from_cooldown(1, cooldown_sec, commands.BucketType.user)
        self.blacklisted_users = set()
        self._load_blacklist()
        owner_id = env_config.get_id('OWNER_ID')
        if owner_id:
            self.owner_id = owner_id

    def _load_blacklist(self):
        try:
            with sqlite3.connect(env_config.DB_PATH) as db:
                cursor = db.cursor()
                cursor.execute("SELECT user_id FROM blacklist")
                self.blacklisted_users = {row[0] for row in cursor.fetchall()}
        except Exception as e:
            print(f"Error loading blacklist: {e}")

    def is_blacklisted(self, user_id: int) -> bool:
        return user_id in self.blacklisted_users

    def add_blacklist(self, user_id: int, reason: str = ""):
        self.blacklisted_users.add(user_id)
        with sqlite3.connect(env_config.DB_PATH) as db:
            db.execute("INSERT OR REPLACE INTO blacklist (user_id, reason) VALUES (?, ?)", (user_id, reason))
            db.commit()

    def remove_blacklist(self, user_id: int):
        self.blacklisted_users.discard(user_id)
        with sqlite3.connect(env_config.DB_PATH) as db:
            db.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
            db.commit()

    async def setup_hook(self):
        for filename in os.listdir(env_config.BASE_DIR / 'cogs'):
            if filename.endswith('.py'):
                await self.load_extension(f'cogs.{filename[:-3]}')

        # Auto-sync slash commands globally so user apps are always available
        try:
            print("[Airi] Registering & syncing slash commands with Discord...")
            synced = await self.tree.sync()
            print(f"[Airi] Successfully synced {len(synced)} global slash command(s). User App installation ready!")
        except Exception as e:
            print(f"[Airi] Slash command sync note: {e}")

client = AiriBot(command_prefix=get_prefix, intents=intents, help_command=None)

@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle slash command errors and report user-friendly ephemeral messages."""
    if isinstance(error, app_commands.CommandInvokeError):
        error = error.original

    if isinstance(error, app_commands.CommandOnCooldown):
        msg = f"⏳ Please slow down! This command is on cooldown for **{error.retry_after:.1f}s**."
    elif isinstance(error, app_commands.CheckFailure):
        msg = "⛔ You do not have permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = f"⚠️ Airi lacks the following permissions: `{', '.join(error.missing_permissions)}`"
    else:
        msg = f"⚠️ An unexpected error occurred while running the slash command:\n`{error}`"
        traceback.print_exception(type(error), error, error.__traceback__)

    embed = discord.Embed(description=msg, color=discord.Color.red())
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print(f"Failed to send interaction error message: {e}")

def get_bot_color(ctx, default=discord.Color.blurple()):
    """Safely get the bot's highest role color in the guild, with fallback for DMs/User Apps."""
    if getattr(ctx, 'guild', None) and client.user:
        member = ctx.guild.get_member(client.user.id)
        if member and member.roles:
            return member.roles[-1].color
    return default

@client.check
async def global_bot_check(ctx):
    # Bot owner is exempt from both cooldown and blacklist
    if client.owner_id and ctx.author.id == client.owner_id:
        return True
    if client.owner_ids and ctx.author.id in client.owner_ids:
        return True
    try:
        if await ctx.bot.is_owner(ctx.author):
            return True
    except Exception:
        pass

    # 1. Blacklist check: reject immediately
    if ctx.bot.is_blacklisted(ctx.author.id):
        raise UserBlacklisted("You are blacklisted from using Airi commands.")

    # 2. 10-second command cooldown per user across all commands
    msg = ctx.message if ctx.message is not None else ctx
    bucket = ctx.bot.cooldown_mapping.get_bucket(msg)
    if bucket:
        retry_after = bucket.update_rate_limit()
        if retry_after:
            raise commands.CommandOnCooldown(bucket, retry_after, commands.BucketType.user)

    return True

def get_personal_channel():
    channel_id = env_config.get_id('PERSONAL_CHANNEL_ID')
    return client.get_channel(channel_id) if channel_id else None

@client.event
async def on_ready():
    print('Airi is ready !')
    await client.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=".help"))
    bot_server = get_personal_channel()
    if bot_server:
        try:
            await bot_server.send(embed=discord.Embed(description=f"Airi is online\n> {datetime.datetime.now()}"))
        except discord.Forbidden:
            print(f"[Warning] Airi lacks 'Send Messages' or 'Embed Links' permission in personal channel {bot_server.id} ({getattr(bot_server, 'name', 'unknown')}).")
        except Exception as e:
            print(f"[Warning] Failed to send startup message: {e}")

@client.event
async def on_guild_join(guild):
    bot_server = get_personal_channel()
    if not bot_server:
        return
    embed = discord.Embed(color=discord.Color.blurple())
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name='Guild Name',value=guild.name,inline=True)
    embed.add_field(name='Guild Id', value=guild.id, inline=True)
    embed.add_field(name='Owner', value=f"<@{guild.owner_id}>", inline=True)
    embed.add_field(name='Member Count', value=guild.member_count, inline=True)
    embed.add_field(name='Created At', value=guild.created_at, inline=True)

    try:
        await bot_server.send(embed=embed)
    except discord.Forbidden:
        print(f"[Warning] Airi lacks permission to send guild join embed in personal channel {bot_server.id}.")
    except Exception as e:
        print(f"[Warning] Failed to send guild join embed: {e}")

# ERROR HANDLING
@client.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.errors.CommandNotFound):
        return
    if isinstance(error, UserBlacklisted):
        return await ctx.send(embed=discord.Embed(
            title="⛔ Access Denied",
            description="You are **blacklisted** from using Airi commands.",
            color=discord.Color.red()
        ))
    elif isinstance(error, commands.CommandOnCooldown):
        return await ctx.send(embed=discord.Embed(
            description=f"⏳ Please slow down! This command is on cooldown for **{error.retry_after:.1f}s**.",
            color=discord.Color.red()
        ))
    elif isinstance(error, commands.NotOwner):
        return await ctx.send(embed=discord.Embed(
            description="⛔ Only the bot **owner** can use this command!",
            color=discord.Color.red()
        ))
    elif isinstance(error, commands.MissingPermissions):
        embed = discord.Embed(description="You don't have permissions to do that!", color=discord.Color.red())
        msg = await ctx.send(embed=embed)
        return await msg.add_reaction("⚠")
    elif isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(embed=discord.Embed(description=f"Missing required argument: `{error.param.name}`", color=discord.Color.red()))
    elif isinstance(error, commands.BadArgument):
        return await ctx.send(embed=discord.Embed(description=f"Invalid argument: {error}", color=discord.Color.red()))

    print(f"Error in command '{ctx.command}': {error}")
    traceback.print_exception(type(error), error, error.__traceback__)

def get_main_help_embed(ctx):
    embed_color = get_bot_color(ctx)
    embed = discord.Embed(title="🌸 Getting Help with Airi", color=embed_color)
    embed.set_thumbnail(url="https://cdn.pixilart.com/photos/large/ffc272ece387503.gif")
    embed.add_field(
        name='Airi is a Multi-Purpose Bot & AI Companion',
        value=(
            '• **👑 Author / Creator:** **Neon Furry** 🐾\n'
            '• **📦 GitHub:** [Sayu69420/airi2.0](https://github.com/Sayu69420/airi2.0)\n'
            '• **Prefix:** `.` (e.g. `.help`, `.chat <message>`, `.ping`)\n'
            '• **Slash Commands:** `/` (e.g. `/chat`, `/imagine`, `/ping`, `/help`)\n\n'
            '🌐 **Use Airi Everywhere!**\n'
            'You can install Airi directly to your Discord account so you can use slash commands '
            'in **any server, DM, or group chat** across Discord!'
        ),
        inline=False
    )
    embed.add_field(name='📋| All Commands', value="`/help category:commands` or `.help commands`", inline=False)
    embed.add_field(name='🌸| Airi AI Companion', value="`/help category:ai` or `.help ai`", inline=True)
    embed.add_field(name='🎵| Music Commands', value="`/help category:music` or `.help music`", inline=True)
    embed.add_field(name="😸| Fun Commands", value="`/help category:fun` or `.help fun`", inline=True)
    embed.add_field(name="⚙| Manage Server", value="`/help category:server` or `.help server`", inline=False)
    embed.add_field(name="❤️| Liked Songs", value="`/help category:liked` or `.help liked`", inline=True)
    embed.set_footer(text="Created with 💖 by Neon Furry | Airi 2.0")
    return embed

def get_help_view(client_user_id=None):
    invite_url = env_config.get_bot_invite_url(client_user_id)
    user_install_url = env_config.get_user_install_url(client_user_id)
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Add to Server", url=invite_url, style=discord.ButtonStyle.link, emoji="🤖"))
    view.add_item(discord.ui.Button(label="Add to Profile (Everywhere)", url=user_install_url, style=discord.ButtonStyle.link, emoji="🌐"))
    return view

async def show_all_commands(ctx):
    embed_color = get_bot_color(ctx)
    embed = discord.Embed(
        title="📜 | All Commands List",
        description=(
            "Here is the complete list of all commands available in Airi:\n"
            "Prefix: `.` | Slash: `/`\n"
            "*(Install Airi to your profile with `/botinvite` to use slash commands everywhere!)*"
        ),
        color=embed_color
    )

    embed.add_field(
        name="🌸 | Airi AI Companion & AI Horde Commands (moeru-ai/airi)",
        value=(
            "`/chat <prompt>` or `.chat` / `.ask` / `.airi` / `.ai` - Chat with Airi AI companion\n"
            "`/gemini <prompt>` or `.gemini` / `.g` - Chat directly with Google Gemini\n"
            "`/nvidia <prompt>` or `.nvidia` / `.nim` - Chat directly with NVIDIA NIM\n"
            "`/horde <prompt>` or `.horde` / `.aihorde` - Chat directly with AI Horde crowdsourced LLM\n"
            "`/imagine <prompt>` or `.imagine` / `.draw` - Generate AI art via AI Horde Stable Diffusion\n"
            "`/kudos` or `.kudos` - View AI Horde user status, Kudos balance & workers\n"
            "`/horde_models [image|text]` - List top active models on AI Horde\n"
            "`.set_horde_model [name|reset]` - Set default AI Horde text model\n"
            "`.set_provider <gemini|nvidia|horde|auto>` - Set preferred AI provider for channel\n"
            "`/ai_status` or `.ai_status` - Check active AI provider & memory state\n"
            "`/ai_reset` or `.ai_reset` / `.clear_ai` - Reset conversation memory in channel\n"
            "`@Airi <message>` - Mention or reply to Airi for direct chat"
        ),
        inline=False
    )

    embed.add_field(
        name="🎵 | Music Commands",
        value=(
            "`.p` / `.play` `<song/url>` - Play or queue a song/playlist (YouTube & Spotify)\n"
            "`.pause` / `.s` - Pause/Resume audio\n"
            "`.np` / `.song` - Display currently playing song\n"
            "`.next` / `.n` / `.skip` - Skip to next song\n"
            "`.prev` / `.ps` - Go to previous song\n"
            "`.jump <num>` / `.j` - Jump to specific song in queue\n"
            "`.queue` / `.q` - Display queued songs\n"
            "`.clear` - Clear song queue\n"
            "`.remove <num>` / `.pop` - Remove song from queue\n"
            "`.loop` / `.l` - Toggle queue/song loop\n"
            "`.shuffle` - Toggle shuffle mode\n"
            "`.leave` - Disconnect bot from voice channel\n"
            "`.grab` - Send current track info via DM"
        ),
        inline=False
    )

    embed.add_field(
        name="❤️ | Liked Songs Commands",
        value=(
            "`.like` - Save current song to your Liked Songs\n"
            "`.get` - View all your Liked Songs\n"
            "`.dl <num>` / `.dislike` - Remove a song from Liked Songs\n"
            "`.pliked [num]` / `.p_liked` - Play all or a specific Liked Song\n"
            "`.spotifyp <name>` / `.spp` - Search and queue Spotify playlist"
        ),
        inline=False
    )
    
    embed.add_field(
        name="⚙️ | Server & Moderation Commands",
        value=(
            "`.prefix [new_prefix]` - View or change server prefix\n"
            "`.mute <user> [mins]` - Mute a server member\n"
            "`.unmute <user>` - Unmute a server member\n"
            "`.del <num>` / `.purge` - Delete messages\n"
            "`/afk [reason]` or `.afk` - Set your AFK status\n"
            "`/ping` or `.ping` - Show bot latency\n"
            "`/botinvite` or `.botinvite` - Get bot invite & user profile install links\n"
            "`.invite [mins|bot]` - Create channel invite link or get bot invite\n"
            "`.join [user]` - Invite members to join your voice channel\n"
            "`.bug <report>` - Send a bug report"
        ),
        inline=False
    )

    embed.add_field(
        name="🎮 | Fun & Reddit Commands",
        value=(
            "`/hi` or `.hi` / `.hello` - Say hello to Airi\n"
            "`/about` or `.about` / `.creator` - About Airi and author Neon Furry\n"
            "`/anime <name>` or `.anime` - Search anime information\n"
            "`/manga <name>` or `.manga` - Search manga information\n"
            "`/pic [user]` or `.pic` - Display user's profile picture\n"
            "`/wallpaper [query]` or `.wall` - Fetch high quality wallpapers\n"
            "`/meme` or `.meme` / `.memes` - Get random memes from Reddit\n"
            "`/reddit [sub]` or `.r` - Fetch top posts from subreddits (`wall`, `anime`, etc.)\n"
            "`.nsfw` - Fetch NSFW Reddit posts (NSFW channels only)"
        ),
        inline=False
    )

    if await client.is_owner(ctx.author):
        embed.add_field(
            name="👑 | Bot Owner Commands",
            value=(
                "`.sync [guild|copy|clear]` - Sync slash application commands\n"
                "`.blacklist <user> [reason]` - Blacklist a user from all commands\n"
                "`.unblacklist <user>` - Remove user from blacklist\n"
                "`.blacklisted` - List all blacklisted users\n"
                "`.reload <cog>` - Reload a cog extension\n"
                "`.load <cog>` / `.unload <cog>` - Load or unload cogs"
            ),
            inline=False
        )

    embed.set_footer(text="Created with 💖 by Neon Furry | Airi 2.0")
    await ctx.send(embed=embed)

async def music_help(ctx):
    embed = discord.Embed(title="🎵 | Music Help", description="Airi can play/stream music on your discord server.\nIt supports **Youtube** and **Spotify**", color=get_bot_color(ctx))
    embed.set_thumbnail(url="https://cutewallpaper.org/21/tsumiki-miniwa-wallpaper/Tsumiki-miniwa-gif-13-GIF-Images-Download.gif")
    embed.add_field(name="Play music", value="`.p Song Name / Song Link / Playlist Link`", inline=False)
    embed.add_field(name="Play/Pause", value="`.pause` to pause & `.play` to play a paused song\nYou can use `.s` alternatively to toggle play/pause", inline=False)
    embed.add_field(name="Next/Previous", value="`.next` or `.n` to jump to next song in the queue\n`.previous` or `.ps` to go back to previously playing song", inline=False)
    embed.add_field(name="Jump", value='`.jump <number>` or `.j <number>` to jump to a particular song in the queue.', inline=False)
    embed.add_field(name="Now Playing", value="`.np` displays currently playing song", inline=False)
    embed.add_field(name="Queue", value="`.q` or `.queue` displays songs in a queue", inline=False)
    embed.add_field(name="Leave", value="`.leave` leaves the voice channel and clears the queue!", inline=False)
    embed.add_field(name="Liked Songs", value="Create your Own playlist of favourite songs!\nFor more info use `/help category:liked` or `.help liked`", inline=False)
    await ctx.send(embed=embed)

async def server_help(ctx):
    embed = discord.Embed(
        title="⚙ | Server Help",
        description="Use `.<command name>` to activate!\nYou need to give **certain permissions** to run a command!",
        color=get_bot_color(ctx)
    )
    embed.set_thumbnail(url="https://media4.giphy.com/media/Fo5y4K3GD3RYijvsCS/giphy.gif")
    embed.add_field(name="Mute/Unmute", value="> `.mute <username> <duration>` default duration: forever `.unmute <@username>`", inline=True)
    embed.add_field(name="Purge Messages", value="> `.del <number>` or `.purge <number>`", inline=True)
    embed.add_field(name="Invite Links", value="> `.invite [mins]` Gives channel invite.\n> `/botinvite` or `.botinvite` Gives Airi bot server and profile install links.", inline=False)
    embed.add_field(name="Ping", value="> `/ping` or `.ping` gives ping of the bot", inline=False)
    await ctx.send(embed=embed)

async def fun_help(ctx):
    embed = discord.Embed(title="🎮 | Fun Commands", description="Use slash commands `/` or prefix `.` to activate!\n", color=get_bot_color(ctx))
    embed.set_thumbnail(url='https://i.gifer.com/Vx3x.gif')
    embed.add_field(name='Anime/Manga', value="> `/anime <name>` or `/manga <name>`", inline=False)
    embed.add_field(name='Wallpapers', value="> `/wallpaper [query]` or `.wall [query]` get high resolution wallpapers", inline=False)
    embed.add_field(name='Profile Picture', value='> `/pic [user]` or `.pic <@member>` get full size avatar', inline=False)
    embed.add_field(name='Reddit Memes', value='> `/meme` or `/reddit [subreddit]`', inline=False)
    await ctx.send(embed=embed)

async def liked_help(ctx):
    embed = discord.Embed(
        title="♥ | Liked Songs Commands",
        description="Airi allows you to save your favourite songs in cloud.\nYou can play your favourite songs anytime from any server.",
        color=get_bot_color(ctx)
    )
    embed.set_thumbnail(url="https://media1.giphy.com/media/UXFWIX08KNkBVdg5Iy/giphy.gif?cid=6c09b952p6uen16tvz1yyvky2xlndq2uvy0r15y9fk69ntcl&rid=giphy.gif&ct=s")
    embed.add_field(name='Like a song', value="> `.like` Adds the song to your Liked songs playlist.", inline=True)
    embed.add_field(name='Dislike', value='> `.dl <number>` or `.dislike <no.>`', inline=True)
    embed.add_field(name="Show Liked", value="> `.get` Shows all of your liked songs", inline=False)
    embed.add_field(name="Play Selected", value="> `.pliked <no.>` Plays the specific song from Liked Songs", inline=False)
    embed.add_field(name='Play All', value="> `.pliked` Plays all Liked songs that You've added.", inline=False)
    await ctx.send(embed=embed)

async def ai_help(ctx):
    embed = discord.Embed(
        title="🌸 | Airi AI Companion Help",
        description="Airi is powered by an AI companion engine inspired by **moeru-ai/airi**!\nShe remembers conversation history per channel and supports local & cloud LLMs.",
        color=discord.Color.pink()
    )
    embed.set_thumbnail(url="https://cdn.pixilart.com/photos/large/ffc272ece387503.gif")
    embed.add_field(name="💬 Chat with Airi", value="> `/chat <message>` or `.chat` / `.ask` / `.airi`", inline=False)
    embed.add_field(name="🌸 Direct Gemini Chat", value="> `/gemini <prompt>` or `.gemini <prompt>` - Chat with Google Gemini", inline=False)
    embed.add_field(name="⚡ Direct NVIDIA NIM Chat", value="> `/nvidia <prompt>` or `.nvidia <prompt>` - Chat with NVIDIA NIM", inline=False)
    embed.add_field(name="🌐 Direct AI Horde Chat", value="> `/horde <prompt>` or `.horde <prompt>` - Free crowdsourced LLMs", inline=False)
    embed.add_field(name="🎨 AI Image Generation", value="> `/imagine <prompt>` or `.imagine <prompt>` - Stable Diffusion via AI Horde", inline=False)
    embed.add_field(name="⭐ AI Horde Kudos & Account", value="> `/kudos` or `.kudos` - View Kudos balance, worker count, and usage stats", inline=False)
    embed.add_field(name="📋 AI Horde Model List", value="> `/horde_models [image|text]` - View active cluster models & workers", inline=False)
    embed.add_field(name="🔧 Set AI Horde Text Model", value="> `.set_horde_model <name|reset>` - Configure default text model", inline=False)
    embed.add_field(name="🎛️ Set Preferred Provider", value="> `.set_provider <gemini|nvidia|horde|auto>` - Switch channel's default AI", inline=False)
    embed.add_field(name="🏷️ Mention & Reply", value="> Mention `@Airi <message>` or reply to any of Airi's messages directly!", inline=False)
    embed.add_field(name="⚙️ AI Engine Status", value="> `/ai_status` or `.ai_status` - Check active provider (Gemini, NVIDIA, Horde, etc.)", inline=False)
    embed.add_field(name="🧹 Reset Memory", value="> `/ai_reset` or `.ai_reset` - Clears conversation history for this chat", inline=False)
    await ctx.send(embed=embed)

@client.hybrid_command(name="help", description="Getting help and information about Airi commands")
@app_commands.describe(category="Category of commands (e.g. ai, music, fun, server, liked, commands)")
@app_commands.choices(category=[
    app_commands.Choice(name="📋 All Commands", value="commands"),
    app_commands.Choice(name="🌸 AI Companion & Horde", value="ai"),
    app_commands.Choice(name="🎵 Music Commands", value="music"),
    app_commands.Choice(name="🎮 Fun & Anime", value="fun"),
    app_commands.Choice(name="⚙️ Server Management", value="server"),
    app_commands.Choice(name="❤️ Liked Songs", value="liked"),
])
async def help_command(ctx, category: str = None):
    if not category:
        embed = get_main_help_embed(ctx)
        view = get_help_view(client.user.id if client.user else None)
        return await ctx.send(embed=embed, view=view)

    cat = category.lower().strip()
    if cat in ["commands", "all", "list"]:
        await show_all_commands(ctx)
    elif cat in ["ai", "airi", "chat"]:
        await ai_help(ctx)
    elif cat in ["music"]:
        await music_help(ctx)
    elif cat in ["server"]:
        await server_help(ctx)
    elif cat in ["fun"]:
        await fun_help(ctx)
    elif cat in ["liked"]:
        await liked_help(ctx)
    else:
        embed = get_main_help_embed(ctx)
        view = get_help_view(client.user.id if client.user else None)
        await ctx.send(embed=embed, view=view)
# COGS

# Owner-only: without the check, any user in any server could unload the bot's
# cogs.
@client.command()
@commands.is_owner()
async def load(ctx, extension):
    await client.load_extension(f'cogs.{extension}')
    await ctx.send(f"Loaded `cogs.{extension}`")

@client.command()
@commands.is_owner()
async def unload(ctx, extension):
    await client.unload_extension(f'cogs.{extension}')
    await ctx.send(f"Unloaded `cogs.{extension}`")

@client.command()
@commands.is_owner()
async def reload(ctx, extension):
    await client.reload_extension(f'cogs.{extension}')
    await ctx.send(f"Reloaded `cogs.{extension}`")

# BLACKLIST MANAGEMENT (OWNER ONLY)
@client.command(name="blacklist", aliases=["block"])
@commands.is_owner()
async def blacklist_command(ctx, target: str = None, *, reason: str = "No reason provided"):
    """Blacklist a user from using any bot commands (Owner only)"""
    if not target or target.lower() in ["list", "show", "all"]:
        return await show_blacklisted(ctx)

    clean = target.replace("<@", "").replace("!", "").replace(">", "").strip()
    if not clean.isdigit():
        return await ctx.send(embed=discord.Embed(
            description="❌ Please specify a valid user mention or user ID. Example: `.blacklist @user spamming`",
            color=discord.Color.red()
        ))

    user_id = int(clean)
    if client.owner_id and user_id == client.owner_id:
        return await ctx.send(embed=discord.Embed(description="❌ You cannot blacklist the bot owner!", color=discord.Color.red()))

    try:
        user_obj = await client.fetch_user(user_id)
        if await client.is_owner(user_obj):
            return await ctx.send(embed=discord.Embed(description="❌ You cannot blacklist the bot owner!", color=discord.Color.red()))
        user_disp = f"**{user_obj.name}** (`{user_id}`)"
    except Exception:
        user_disp = f"User `{user_id}`"

    if client.is_blacklisted(user_id):
        return await ctx.send(embed=discord.Embed(
            description=f"⚠️ {user_disp} is **already** blacklisted.",
            color=discord.Color.orange()
        ))

    client.add_blacklist(user_id, reason=reason)
    embed = discord.Embed(
        title="⛔ User Blacklisted",
        description=f"Successfully blacklisted {user_disp} from using Airi commands.\n**Reason:** {reason}",
        color=discord.Color.red()
    )
    await ctx.send(embed=embed)


@client.command(name="unblacklist", aliases=["unblock"])
@commands.is_owner()
async def unblacklist_command(ctx, target: str = None):
    """Remove a user from the blacklist (Owner only)"""
    if not target:
        return await ctx.send(embed=discord.Embed(
            description="❌ Please specify a user to unblacklist. Example: `.unblacklist @user` or `.unblacklist 123456789`",
            color=discord.Color.red()
        ))

    clean = target.replace("<@", "").replace("!", "").replace(">", "").strip()
    if not clean.isdigit():
        return await ctx.send(embed=discord.Embed(description="❌ Please specify a valid user mention or ID.", color=discord.Color.red()))

    user_id = int(clean)
    if not client.is_blacklisted(user_id):
        return await ctx.send(embed=discord.Embed(
            description=f"⚠️ User `{user_id}` is **not** currently blacklisted.",
            color=discord.Color.orange()
        ))

    client.remove_blacklist(user_id)
    await ctx.send(embed=discord.Embed(
        title="✅ User Unblacklisted",
        description=f"Successfully removed user `{user_id}` from the blacklist.",
        color=discord.Color.green()
    ))


@client.command(name="blacklisted", aliases=["blacklist_list"])
@commands.is_owner()
async def show_blacklisted(ctx):
    """View all blacklisted users (Owner only)"""
    try:
        with sqlite3.connect(env_config.DB_PATH) as db:
            cursor = db.cursor()
            cursor.execute("SELECT user_id, reason, added_at FROM blacklist ORDER BY added_at DESC")
            rows = cursor.fetchall()
    except Exception as e:
        return await ctx.send(f"Error fetching blacklist: {e}")

    if not rows:
        return await ctx.send(embed=discord.Embed(
            description="✨ There are currently **no** blacklisted users.",
            color=discord.Color.green()
        ))

    embed = discord.Embed(
        title="⛔ Blacklisted Users",
        description=f"Total blacklisted: **{len(rows)}** user(s)\n*(Blacklisted users cannot use any commands or AI features)*",
        color=discord.Color.red()
    )
    for user_id, reason, added_at in rows[:25]:
        embed.add_field(
            name=f"User ID: `{user_id}`",
            value=f"• **Reason:** {reason or 'None'}\n• **Date:** {added_at or 'Unknown'}",
            inline=False
        )
    await ctx.send(embed=embed)

@client.hybrid_command(name="botinvite", aliases=["invitebot", "addbot", "install"], description="Get invite links to add Airi to servers or install to your account to use everywhere!")
async def bot_invite_command(ctx):
    """Get the invite link to add Airi to your Discord server or profile."""
    invite_url = env_config.get_bot_invite_url(client.user.id if client.user else None)
    user_install_url = env_config.get_user_install_url(client.user.id if client.user else None)
    embed = discord.Embed(
        title="🤖 Invite Airi or Install to Profile",
        description=(
            f"You can add **Airi** in two ways:\n\n"
            f"🏠 **1. Add to Server (Guild Install)**\n"
            f"Invite Airi to your Discord server for music streaming, moderation, and server AI chat:\n"
            f"🔗 **[Click here to Invite Airi to Server]({invite_url})**\n\n"
            f"🌐 **2. Add to Profile (User App - Use Everywhere!)**\n"
            f"Install Airi directly to your Discord account to use slash commands (`/chat`, `/imagine`, `/ping`, `/anime`, etc.) "
            f"**everywhere across Discord**, including DMs, group chats, and servers where Airi is not even added!\n"
            f"🔗 **[Click here to Install Airi to Profile]({user_install_url})**\n\n"
            f"*(Make sure User Install is enabled in your Discord Developer Portal under Installation)*"
        ),
        color=get_bot_color(ctx)
    )
    if client.user and client.user.avatar:
        embed.set_thumbnail(url=client.user.avatar.url)
    embed.set_footer(text="Created with 💖 by Neon Furry | Airi 2.0")
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Add to Server", url=invite_url, style=discord.ButtonStyle.link, emoji="🤖"))
    view.add_item(discord.ui.Button(label="Add to Profile (Everywhere)", url=user_install_url, style=discord.ButtonStyle.link, emoji="🌐"))
    view.add_item(discord.ui.Button(label="GitHub", url="https://github.com/Sayu69420/airi2.0", style=discord.ButtonStyle.link, emoji="⭐"))
    await ctx.send(embed=embed, view=view)

@client.command(name="sync")
@commands.is_owner()
async def sync_command(ctx, spec: str = None):
    """Sync application slash commands with Discord (Owner only).
    Usage:
      .sync         - Sync all global commands (makes them available everywhere)
      .sync guild   - Sync commands to the current guild immediately (instant test)
      .sync copy    - Copy global commands to current guild and sync
      .sync clear   - Clear commands from current guild
    """
    if spec in ["guild", "~", "this"]:
        if not ctx.guild:
            return await ctx.send("Guild sync can only be run inside a Discord server.")
        synced = await ctx.bot.tree.sync(guild=ctx.guild)
        await ctx.send(f"✅ Synced **{len(synced)}** slash command(s) to this guild (`{ctx.guild.name}`).")
    elif spec == "copy":
        if not ctx.guild:
            return await ctx.send("Copy can only be run inside a Discord server.")
        ctx.bot.tree.copy_global_to(guild=ctx.guild)
        synced = await ctx.bot.tree.sync(guild=ctx.guild)
        await ctx.send(f"✅ Copied and synced **{len(synced)}** global command(s) to this guild.")
    elif spec == "clear":
        if not ctx.guild:
            return await ctx.send("Clear can only be run inside a Discord server.")
        ctx.bot.tree.clear_commands(guild=ctx.guild)
        await ctx.bot.tree.sync(guild=ctx.guild)
        await ctx.send(f"🧹 Cleared slash commands from this guild.")
    else:
        async with ctx.typing():
            synced = await ctx.bot.tree.sync()
        await ctx.send(
            f"🌐 Successfully synced **{len(synced)}** global slash command(s) across Discord!\n"
            f"*(Members can now install and use commands everywhere!)*"
        )

# run command
if __name__ == '__main__':
    token = env_config.get('DISCORD_BOT_TOKEN')
    if token:
        client.run(token)
    else:
        print(
            "Error: DISCORD_BOT_TOKEN is missing.\n"
            f"Copy .env.sample to {env_config.BASE_DIR / '.env'} and fill in your bot token."
        )
