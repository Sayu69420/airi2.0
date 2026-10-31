import discord, datetime, random, yt_dlp, os, asyncio
from discord.ext import commands
from discord.voice_client import VoiceClient
import sqlite3
from dotenv import load_dotenv

load_dotenv()

def get_prefix(bot, msg):
    if not msg.guild:
        return commands.when_mentioned_or(".")(bot, msg)
    try:
        with sqlite3.connect("config.db") as db:
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

class AiriBot(commands.Bot):
    async def setup_hook(self):
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                await self.load_extension(f'cogs.{filename[:-3]}')

client = AiriBot(command_prefix=get_prefix, intents=intents, help_command=None)

def get_personal_channel():
    raw_id = os.getenv('PERSONAL_CHANNEL_ID')
    if raw_id and raw_id.isdigit():
        return client.get_channel(int(raw_id))
    return None

@client.event
async def on_ready():
    print('Airi is ready !')
    await client.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=".help"))
    bot_server = get_personal_channel()
    if bot_server:
        await bot_server.send(embed=discord.Embed(description=f"Airi is online\n> {datetime.datetime.now()}"))

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

    await bot_server.send(embed=embed)

# ERROR HANDLING
@client.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.errors.CommandNotFound):
        return
    import traceback
    print(f"Error in command '{ctx.command}': {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if isinstance(error, commands.MissingPermissions):
        embed = discord.Embed(description="You don't have permissions to do that!", color=discord.Color.red())
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("⚠")

@client.group(invoke_without_command=True)
async def help(ctx):
    bot_roles = ctx.guild.get_member(client.user.id).roles if ctx.guild else None
    embed_color = bot_roles[len(bot_roles)-1].color if bot_roles else discord.Color.blurple()
    embed = discord.Embed(title= "Getting Help",color=embed_color)
    embed.set_thumbnail(
        url="https://cdn.pixilart.com/photos/large/ffc272ece387503.gif")

    embed.add_field(
        name='Airi is a Multi-Purpose Bot',
        value='My Prefix is = `.`\n Use following commands for extended information on a command',inline=False)

    embed.add_field(name='📋| All Commands',value=".help commands",inline=False)
    embed.add_field(name='🌸| Airi AI Companion',value=".help ai",inline=True)
    embed.add_field(name='🎵| Music Commands',value=".help music",inline=True)
    embed.add_field(name="😸| Fun Commands",value=".help fun",inline=True)
    embed.add_field(name="⚙| Manage Server",value=".help server",inline=False)
    embed.add_field(name="🧬| Covid-19",value=".help covid",inline=True)
    embed.add_field(name="\u200b", value="[Add me to your server !](https://discord.com/oauth2/authorize?client_id=1532368781094162472&permissions=8&integration_type=0&scope=bot)",inline=False)
    await ctx.send(embed=embed)

@help.command(name="commands", aliases=["all", "list"])
async def show_all_commands(ctx):
    bot_roles = ctx.guild.get_member(client.user.id).roles if ctx.guild else None
    embed_color = bot_roles[len(bot_roles)-1].color if bot_roles else discord.Color.blurple()

    embed = discord.Embed(
        title="📜 | All Commands List",
        description="Here is the complete list of all commands available in Airi:\nPrefix: `.`",
        color=embed_color
    )

    embed.add_field(
        name="🌸 | Airi AI Companion Commands (moeru-ai/airi)",
        value=(
            "`.chat <prompt>` / `.ask` / `.airi` / `.ai` - Chat with Airi AI companion\n"
            "`.ai_status` / `.airi_status` - Check active AI provider & memory state\n"
            "`.ai_reset` / `.clear_ai` - Reset conversation memory in channel\n"
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
            "`.afk [reason]` - Set your AFK status\n"
            "`.ping` - Show bot latency\n"
            "`.invite [mins]` - Create channel invite link\n"
            "`.join [user]` - Invite members to join your voice channel\n"
            "`.bug <report>` - Send a bug report"
        ),
        inline=False
    )

    embed.add_field(
        name="🎮 | Fun & Reddit Commands",
        value=(
            "`.hi` / `.hello` - Say hello to Airi\n"
            "`.anime <name>` - Search anime information\n"
            "`.manga <name>` - Search manga information\n"
            "`.pic <member>` - Display user's profile picture\n"
            "`.meme` / `.memes` - Get random memes from Reddit\n"
            "`.r <sub/arg>` - Fetch top posts from subreddits (`wall`, `anime`, etc.)\n"
            "`.nsfw` - Fetch NSFW Reddit posts (NSFW channels only)"
        ),
        inline=False
    )

    embed.add_field(
        name="🧬 | Covid-19 Information",
        value=(
            "`.cinfo` - COVID-19 general information\n"
            "`.cc [country]` - COVID-19 statistics by country\n"
            "`.cmed` - General medicine & dosage list\n"
            "`.cvac` - COVID-19 vaccination details and links"
        ),
        inline=False
    )

    await ctx.send(embed=embed)

@help.command()
async def music(ctx):
    embed = discord.Embed(title= "🎵 | Music Help",description="Airi can play/stream music on your discord server.\nIt supports **Youtube** and **Spotify**",color=discord.Color.blurple())
    embed.set_thumbnail(
        url="https://cutewallpaper.org/21/tsumiki-miniwa-wallpaper/Tsumiki-miniwa-gif-13-GIF-Images-Download.gif"
    )
    embed.add_field(
        name ="Play music",value="`.p Song Name / Song Link / Playlist Link` ",inline=False)
    embed.add_field(name="Play/Pause",value="`.pause`to pause & `.play` to play a paused song\n You can use `.s` alternatively to toggle play/pause",inline=False)
    embed.add_field(name="Next/Previous",value="`.next` or `.n` to jump to next song in the queue\n `.previous`or `.ps` to go back to previously playing song",inline=False)
    embed.add_field(name="Jump",value='`.jump <number>` or `.j <number>` to jump in a perticular song in the queue.',inline=False)
    embed.add_field(name="Now Playing",value="`.np` displays currently playing song",inline=False)
    embed.add_field(name="Queue",value="`.q` or `.queue` displays songs in a queue",inline=False)
    embed.add_field(name="Leave",value="`.leave` leaves the voice channel and clears the queue !",inline=False)
    embed.add_field(name="Liked Songs",value="Create your Own playlist of favourite songs !\nFor more info use `.help liked`",inline=False)
    await ctx.send(embed=embed)

@help.command()
async def covid(ctx):
    bot_roles = ctx.guild.get_member(client.user.id).roles
    embed = discord.Embed(title= "🧬 | Covid-19 Help",description="Use `.<command name>` to activate !",color=bot_roles[len(bot_roles)-1].color)
    embed.set_thumbnail(
        url="https://media0.giphy.com/media/hubAFdn7wrjM3SVkq5/source.gif")

    embed.add_field(name='😷 | About Covid',value="`.cinfo` Gives the Covid-19 information.",inline=False)
    embed.add_field(name="📈 | Covid Cases",value="`.cc <country>` Use this command to get Covid-19 stats in a country (default:India)\nFor eg: cc usa to get Covid stats of United States",inline=False)
    embed.add_field(name="🩹 | General Medicines",value="`.cmed` Gives general purpose Medicines name and their Dosage",inline=False)
    embed.add_field(name="💉 | Vaccine Information",value="`.cvac` Gives information and important url related to Vaccination in India",inline=False)


    await ctx.send(embed=embed)

@help.command()
async def server(ctx):
    bot_roles = ctx.guild.get_member(client.user.id).roles
    embed = discord.Embed(title="⚙ | Server Help",description="Use `.<command name` to activate !\n You need to give **certain permissions** to run a command !\n List of permissions can be found inside description of each command !",color=bot_roles[len(bot_roles)-1].color)
    embed.set_thumbnail(
        url = "https://media4.giphy.com/media/Fo5y4K3GD3RYijvsCS/giphy.gif")
    embed.add_field(name="Server Info",value = "`.si` gives information about Server",inline=False)
    embed.add_field(name="Mute/Unmute",value="> `.mute <username> <duration>` default duration : forever `.unmute <@username>`",inline=True)
    embed.add_field(name="Ban/Unban", value="> `.ban <username>` &  `.unban <@username>`", inline=True)

    embed.add_field(name='Kick',value="> `.kick <@username>` Required Permissions : Kick Members",inline=False)
    embed.add_field(name='Invite',value= "> `.invite` Gives the Invite link of Server.",inline=True)

    embed.add_field(name='Delete Messsages',value="> `.del <number>` or `.clear <number>`",inline=True)
    embed.add_field(name="Ping",value="> `.ping` gives ping of the server",inline=False)

    await ctx.send(embed=embed)

@help.command()
async def fun(ctx):
    bot_roles = ctx.guild.get_member(client.user.id).roles
    embed = discord.Embed(title="🎮 | Fun Commands",description="Use `.<command name>` to activate !\n",color=bot_roles[len(bot_roles)-1].color)
    embed.set_thumbnail(url='https://i.gifer.com/Vx3x.gif')
    embed.add_field(name='Anime/Manga',value="> `.anime <name>` or `.manga <name>`",inline=False)
    embed.add_field(name='Wallpapers',value="> `.wall` get any random wallpaper\n> `.wall <name>` get wallpapers in the mentioned category")
    embed.add_field(name='Profile picture',value=' `.pic <@member>`')

    await ctx.send(embed=embed)

@help.command()
async def liked(ctx):
    bot_roles = ctx.guild.get_member(client.user.id).roles
    embed = discord.Embed(title="♥ | Liked Commands",description="Airi allows you to save your favourite songs in cloud.\nYou can play your favourite songs anytime from any server.", color=bot_roles[len(bot_roles)-1].color)
    embed.set_thumbnail(url="https://media1.giphy.com/media/UXFWIX08KNkBVdg5Iy/giphy.gif?cid=6c09b952p6uen16tvz1yyvky2xlndq2uvy0r15y9fk69ntcl&rid=giphy.gif&ct=s")
    embed.add_field(name='Like a song',value="> `.like` Adds the song to your Liked songs playlist.",inline=True)
    embed.add_field(name='Dislike',value='> `.dl <number>` or `.dislike <no.>`',inline=True)
    embed.add_field(name="Show Liked",value="> `.get` Shows all of your liked songs",inline=False)
    embed.add_field(name="Play Selected",value="> `.pliked <no.>` Plays the specific song from Liked Songs",inline=False)
    embed.add_field(name='Play All',value="> `.pliked` Plays all Liked songs that You've added.",inline=False)
    await ctx.send(embed=embed)

@help.command(name="ai", aliases=["airi", "chat"])
async def ai_help(ctx):
    bot_roles = ctx.guild.get_member(client.user.id).roles if ctx.guild else None
    embed_color = bot_roles[len(bot_roles)-1].color if bot_roles else discord.Color.pink()
    embed = discord.Embed(
        title="🌸 | Airi AI Companion Help",
        description="Airi is powered by an AI companion engine inspired by **moeru-ai/airi**!\nShe remembers conversation history per channel and supports local & cloud LLMs.",
        color=embed_color
    )
    embed.set_thumbnail(url="https://cdn.pixilart.com/photos/large/ffc272ece387503.gif")
    embed.add_field(name="💬 Chat with Airi", value="> `.chat <your question>` or `.ask` / `.airi` / `.ai`", inline=False)
    embed.add_field(name="🏷️ Mention & Reply", value="> Mention `@Airi <message>` or reply to any of Airi's messages directly!", inline=False)
    embed.add_field(name="⚙️ AI Engine Status", value="> `.ai_status` - Check active provider (Local Moeru-AI, OpenRouter, Groq, OpenAI, Gemini, Ollama)", inline=False)
    embed.add_field(name="🧹 Reset Memory", value="> `.ai_reset` or `.clear_ai` - Clears conversation history for the current channel", inline=False)
    await ctx.send(embed=embed)
# COGS

@client.command()
async def load(ctx, extension):
    await client.load_extension(f'cogs.{extension}')
    await ctx.send(f"Loaded `cogs.{extension}`")

@client.command()
async def unload(ctx, extension):
    await client.unload_extension(f'cogs.{extension}')
    await ctx.send(f"Unloaded `cogs.{extension}`")

@client.command()
async def reload(ctx, extension):
    await client.reload_extension(f'cogs.{extension}')
    await ctx.send(f"Reloaded `cogs.{extension}`")

# run command
token = os.getenv('DISCORD_BOT_TOKEN')
if token:
    client.run(token)
else:
    print("Error: DISCORD_BOT_TOKEN environment variable is missing in .env file.")
