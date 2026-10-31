import discord, asyncio, random, requests
from discord.ext import commands
from discord import app_commands

import env_config

queue1 = []

class Task(commands.Cog):
    def __init__(self,client):
        self.client = client
        self.r_store = []

    @commands.hybrid_command(name="hi", aliases=['hii','hello'], description="Say hello to Airi")
    async def _hey(self,ctx):
        await ctx.reply("Hello ! 🌸")
        if not ctx.interaction and getattr(ctx, 'message', None):
            try:
                await ctx.message.add_reaction("🧡")
            except Exception:
                pass

    @commands.hybrid_command(name="about", aliases=['creator', 'author'], description="About Airi and author Neon Furry")
    async def about_command(self, ctx):
        """Information about Airi and author Neon Furry."""
        invite_url = env_config.get_bot_invite_url(self.client.user.id if self.client.user else None)
        user_install_url = env_config.get_user_install_url(self.client.user.id if self.client.user else None)

        embed = discord.Embed(
            title="🌸 About Airi & Creator",
            description=(
                "**Airi 2.0** is an intelligent, sweet, and cute multi-purpose Discord bot & AI companion.\n"
                "Members can use Airi in servers or install her directly to their user profiles to chat and use slash commands everywhere across Discord!\n\n"
                "👑 **Author / Creator:** **Neon Furry** 🐾\n"
                "📦 **GitHub:** [Sayu69420/airi2.0](https://github.com/Sayu69420/airi2.0)\n"
                "💡 **Inspired By:** [moeru-ai/airi](https://github.com/moeru-ai/airi)\n"
                "⚡ **Framework:** discord.py 2.7\n"
                "✨ **Features:** AI Companion (`/chat`), Image Generation (`/imagine`), Music Streaming, Memes, Anime/Manga, and User App Install"
            ),
            color=discord.Color.from_rgb(255, 182, 193)
        )
        if self.client.user and self.client.user.avatar:
            embed.set_thumbnail(url=self.client.user.avatar.url)
        embed.set_footer(text="Created with 💖 by Neon Furry | Airi 2.0")

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Invite to Server", url=invite_url, style=discord.ButtonStyle.link, emoji="🤖"))
        view.add_item(discord.ui.Button(label="Install to Profile (Everywhere)", url=user_install_url, style=discord.ButtonStyle.link, emoji="🌐"))
        view.add_item(discord.ui.Button(label="GitHub", url="https://github.com/Sayu69420/airi2.0", style=discord.ButtonStyle.link, emoji="⭐"))
        await ctx.send(embed=embed, view=view)

    @commands.hybrid_command(name="anime", description="Search anime information")
    @app_commands.describe(title="Name of the anime")
    async def anime_(self, ctx, *, title: str):
        arg = title
        try:
            headers = {'User-Agent': 'AiriBot/1.0'}
            response = await asyncio.to_thread(
                requests.get, 'https://api.jikan.moe/v4/anime',
                params={'q': arg, 'limit': 1}, headers=headers, timeout=10)
            res_json = response.json() if response.status_code == 200 else {}
            if response.status_code == 429:
                return await ctx.send(embed=discord.Embed(description="Jikan rate limit hit (3 req/sec). Try again in a moment!", color=discord.Color.orange()))
            if response.status_code != 200 or not res_json.get('data'):
                print(f"Jikan anime lookup failed: HTTP {response.status_code} for {arg!r}")
                return await ctx.send(embed=discord.Embed(description="Anime not found or API unavailable!", color=discord.Color.red()))

            anime_dict = res_json['data'][0]
            title = anime_dict.get('title', 'Unknown')
            image_url = anime_dict.get('images', {}).get('jpg', {}).get('image_url', '')
            score = anime_dict.get('score', 'N/A')
            episodes = anime_dict.get('episodes', 'N/A')
            rated = anime_dict.get('rating', 'N/A')

            aired = anime_dict.get('aired', {})
            start_date = aired.get('from', '')[:10] if aired and aired.get('from') else 'N/A'
            end_date = aired.get('to', '')[:10] if aired and aired.get('to') else 'Currently Airing'
            synopsis = anime_dict.get('synopsis', 'No storyline available.')
            url = anime_dict.get('url', '')

            embed = discord.Embed(title=title, color=discord.Color.orange())
            if image_url:
                embed.set_thumbnail(url=image_url)
            embed.add_field(name="🌟 | Rating", value=f"{score}/10", inline=True)
            embed.add_field(name="🍿 | Episodes", value=f"{episodes}", inline=True)
            embed.add_field(name="🎬 | Rated", value=f"{rated}", inline=False)
            embed.add_field(name="Release Date", value=f"{start_date}", inline=True)
            embed.add_field(name="Finished Airing", value=f"{end_date}", inline=True)
            if synopsis:
                syn_text = synopsis[:800] + "..." if len(synopsis) > 800 else synopsis
                embed.add_field(name="Storyline", value=f"{syn_text}\n **[click to see more]({url})**", inline=False)

            await ctx.send(embed=embed)
        except Exception as e:
            print(f"Anime lookup error: {type(e).__name__}: {e}")
            await ctx.send(embed=discord.Embed(description="Failed to get anime info. Try again later!", color=discord.Color.red()))

    @commands.hybrid_command(name="manga", description="Search manga information")
    @app_commands.describe(title="Name of the manga")
    async def manga_(self, ctx, *, title: str):
        arg = title
        try:
            headers = {'User-Agent': 'AiriBot/1.0'}
            response = await asyncio.to_thread(
                requests.get, 'https://api.jikan.moe/v4/manga',
                params={'q': arg, 'limit': 1}, headers=headers, timeout=10)
            res_json = response.json() if response.status_code == 200 else {}
            if response.status_code == 429:
                return await ctx.send(embed=discord.Embed(description="Jikan rate limit hit (3 req/sec). Try again in a moment!", color=discord.Color.orange()))
            if response.status_code != 200 or not res_json.get('data'):
                print(f"Jikan manga lookup failed: HTTP {response.status_code} for {arg!r}")
                return await ctx.send(embed=discord.Embed(description="Manga not found or API unavailable!", color=discord.Color.red()))

            manga_dict = res_json['data'][0]
            title = manga_dict.get('title', 'Unknown')
            image_url = manga_dict.get('images', {}).get('jpg', {}).get('image_url', '')
            score = manga_dict.get('score', 'N/A')
            chapters = manga_dict.get('chapters', 'N/A')
            volumes = manga_dict.get('volumes', 'N/A')

            published = manga_dict.get('published', {})
            start_date = published.get('from', '')[:10] if published and published.get('from') else 'N/A'
            end_date = published.get('to', '')[:10] if published and published.get('to') else 'Currently Publishing'
            synopsis = manga_dict.get('synopsis', 'No storyline available.')
            url = manga_dict.get('url', '')

            embed = discord.Embed(title=title, color=discord.Color.orange())
            if image_url:
                embed.set_thumbnail(url=image_url)
            embed.add_field(name="🌟 | Rating", value=f"{score}/10", inline=True)
            embed.add_field(name="🍿 | Chapters", value=f"{chapters}", inline=True)
            embed.add_field(name="📘 | Volumes", value=f"{volumes}", inline=False)
            embed.add_field(name="Release Date", value=f"{start_date}", inline=True)
            embed.add_field(name="Finished Airing", value=f"{end_date}", inline=True)
            if synopsis:
                syn_text = synopsis[:800] + "..." if len(synopsis) > 800 else synopsis
                embed.add_field(name="Storyline", value=f"{syn_text}\n **[click to see more]({url})**", inline=False)

            await ctx.send(embed=embed)
        except Exception as e:
            print(f"Manga lookup error: {type(e).__name__}: {e}")
            await ctx.send(embed=discord.Embed(description="Failed to get manga info. Try again later!", color=discord.Color.red()))

    @commands.hybrid_command(name="pic", aliases=['avatar'], description="Display a user's avatar / profile picture")
    @app_commands.describe(user="User whose profile picture to show (defaults to you)")
    async def get_img(self, ctx, user: discord.User = None):
        person = user or ctx.author
        avatar = person.display_avatar
        pic_url_jpg = avatar.with_format('jpg').with_size(1024).url
        pic_url_jpeg = avatar.with_format('jpeg').with_size(1024).url
        pic_url_png = avatar.with_format('png').with_size(1024).url

        if avatar.is_animated():
            pic_url_gif = avatar.with_format('gif').with_size(1024).url
            embed = discord.Embed(
                title="Profile Picture",
                description=f"**{person.mention} Download : [JPG]({pic_url_jpg}) | [ JPEG ]({pic_url_jpeg}) | [ PNG ]({pic_url_png}) | [ GIF ]({pic_url_gif})**",
                color=discord.Color.from_rgb(121, 192, 212)
            )
            embed.set_image(url=pic_url_gif)
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                title="Profile Picture",
                description=f"**{person.mention} Download : [ JPG ]({pic_url_jpg}) | [ JPEG ]({pic_url_jpeg}) | [ PNG ]({pic_url_png})**",
                color=discord.Color.from_rgb(121, 192, 212)
            )
            embed.set_image(url=pic_url_jpg)
            await ctx.send(embed=embed)

    # @commands.group(invoke_without_command=True)
    @commands.command(aliases=['react','em'])
    async def _replied(self,ctx,*,text:str):
        emoji_list = {' ':'〰','a': '🇦', 'b': '🇧', 'c': '🇨', 'd': '🇩', 'e': '🇪', 'f': '🇫', 'g': '🇬', 'h': '🇭','i': '🇮', 'j': '🇯', 'k': '🇰', 'l': '🇱', 'm': '🇲', 'n': '🇳', 'o': '🇴', 'p': '🇵','q': '🇶', 'r': '🇷', 's': '🇸', 't': '🇹', 'u': '🇺', 'v': '🇻', 'w': '🇼', 'x': '🇽','y': '🇾', 'z': '🇿','0':'0️⃣','1':'1️⃣','2':'2️⃣','3':'3️⃣','4':'4️⃣','5':'5️⃣','6':'6️⃣','7':'7️⃣','8':'8️⃣','9':'9️⃣'}
        msg_id = 0
        if ctx.message.reference != None:
            msg_id =  ctx.message.reference.message_id
        else:
            await ctx.send(embed=discord.Embed(description="Please reply to a message in order to react !",color=discord.Color.red()))
        msg = await ctx.fetch_message(msg_id)
        text = str(text).lower()
        try:
            await ctx.message.delete()
        except discord.HTTPException as e:
            print(f"_replied delete error: {type(e).__name__}: {e}")
        for y in text:
            emoji = emoji_list.get(y)
            if emoji:
                try:
                    await msg.add_reaction(emoji)
                except discord.HTTPException as e:
                    print(f"_replied add_reaction error: {type(e).__name__}: {e}")
        # except:
            # await ctx.send(embed = discord.Embed(description="Referenced message not found !!",color=discord.Color.red()))

    @commands.hybrid_command(name="wallpaper", aliases=['wall'], description="Search and fetch high quality wallpapers")
    @app_commands.describe(query="Wallpaper search term or category")
    async def wallp_(self, ctx, *, query: str = None):
        client_id = env_config.get('UNSPLASH_CLIENT_ID')
        page = random.randint(1, 5)
        headers = {'User-Agent': 'AiriBot/1.0'}

        # 1. Try Unsplash if valid client_id is set
        if client_id:
            try:
                if query is None:
                    url = f'https://api.unsplash.com/topics/wallpapers/photos?client_id={client_id}&page={page}'
                else:
                    url = f'https://api.unsplash.com/search/photos/?query={query}&client_id={client_id}&page={page}'

                resp = await asyncio.to_thread(requests.get, url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data if isinstance(data, list) else data.get('results', [])
                    if results:
                        item = random.choice(results)
                        raw_url = item['urls']['raw']
                        username = item['user']['username']
                        profile_img = item['user']['profile_image']['small']

                        embed = discord.Embed(description=f"**[Original/Raw]({raw_url})**", color=discord.Color.blue())
                        embed.set_image(url=raw_url)
                        embed.set_footer(text=f"Picture from {username}", icon_url=profile_img)
                        msg = await ctx.send(embed=embed)
                        if not ctx.interaction:
                            try:
                                await msg.add_reaction("♥")
                            except Exception:
                                pass
                        return
            except Exception as e:
                print(f"Unsplash API error: {e}")

        # 2. Fallback to Wallhaven API (No API key required)
        try:
            if query is None:
                wh_url = f'https://wallhaven.cc/api/v1/search?categories=111&purity=100&sorting=random&page={page}'
            else:
                wh_url = f'https://wallhaven.cc/api/v1/search?q={query}&purity=100&page={page}'

            resp = await asyncio.to_thread(requests.get, wh_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json().get('data', [])
                if data:
                    item = random.choice(data)
                    img_url = item.get('path')
                    short_url = item.get('url')
                    dimension = f"{item.get('dimension_x')}x{item.get('dimension_y')}"

                    embed = discord.Embed(description=f"**[Original/Full]({img_url})** | [View Page]({short_url})", color=discord.Color.blue())
                    embed.set_image(url=img_url)
                    embed.set_footer(text=f"Resolution: {dimension} | Wallhaven")
                    msg = await ctx.send(embed=embed)
                    if not ctx.interaction:
                        try:
                            await msg.add_reaction("♥")
                        except Exception:
                            pass
                    return
                else:
                    return await ctx.send(embed=discord.Embed(description=f"No wallpapers found for `{query}`!", color=discord.Color.red()))
        except Exception as e:
            print(f"Wallhaven API error: {e}")

        await ctx.send(embed=discord.Embed(description="Failed to get images! Please try again later.", color=discord.Color.red()))



async def setup(client):
    await client.add_cog(Task(client))
