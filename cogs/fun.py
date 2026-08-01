import discord,sqlite3,asyncio,json,random,requests,praw,os
from discord import embeds
from discord.errors import DiscordServerError
from discord.ext import commands

queue1 = []

class Task(commands.Cog):
    def __init__(self,client):
        self.client = client
        self.r_store = []

    @commands.command(aliases=['hi','hii','hello'])
    async def _hey(self,ctx):
        resp = str("Hello !")
        msg = await ctx.reply(resp)
        await ctx.message.add_reaction("🧡")

    @commands.command(aliases=['anime'])
    async def anime_(self, ctx, *, arg):
        try:
            headers = {'User-Agent': 'AiriBot/1.0'}
            response = requests.get(f'https://api.jikan.moe/v4/anime?q={arg}&limit=1', headers=headers, timeout=10)
            res_json = response.json()
            if response.status_code != 200 or 'data' not in res_json or not res_json['data']:
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
            await ctx.send(embed=discord.Embed(description="Failed to get anime info. Try again later!", color=discord.Color.red()))

    @commands.command(aliases=['manga'])
    async def manga_(self, ctx, *, arg):
        try:
            headers = {'User-Agent': 'AiriBot/1.0'}
            response = requests.get(f'https://api.jikan.moe/v4/manga?q={arg}&limit=1', headers=headers, timeout=10)
            res_json = response.json()
            if response.status_code != 200 or 'data' not in res_json or not res_json['data']:
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
            await ctx.send(embed=discord.Embed(description="Failed to get manga info. Try again later!", color=discord.Color.red()))

    @commands.command(aliases=['pic'])
    async def get_img(self, ctx, person: discord.Member):
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
        except:
            pass
        for y in text:
            emoji = emoji_list.get(y)
            if emoji:
                try:
                    await msg.add_reaction(emoji)
                except:
                    pass
        # except:
            # await ctx.send(embed = discord.Embed(description="Referenced message not found !!",color=discord.Color.red()))

    @commands.command(aliases=['wall', 'wallpaper'])
    async def wallp_(self, ctx, *, query: str = None):
        client_id = os.getenv('UNSPLASH_CLIENT_ID', '').strip()
        page = random.randint(1, 5)
        headers = {'User-Agent': 'AiriBot/1.0'}

        # 1. Try Unsplash if valid client_id is set
        if client_id and client_id != 'CLIENT_ID':
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
                        await msg.add_reaction("♥")
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
                    await msg.add_reaction("♥")
                    return
                else:
                    return await ctx.send(embed=discord.Embed(description=f"No wallpapers found for `{query}`!", color=discord.Color.red()))
        except Exception as e:
            print(f"Wallhaven API error: {e}")

        await ctx.send(embed=discord.Embed(description="Failed to get images! Please try again later.", color=discord.Color.red()))



async def setup(client):
    await client.add_cog(Task(client))
