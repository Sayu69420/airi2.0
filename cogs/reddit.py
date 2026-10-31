import discord, praw, random, asyncio
from discord.ext import commands
from discord import app_commands

import env_config

class Reddit(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.reddit = None
        client_id = env_config.get("REDDIT_CLIENT_ID")
        client_secret = env_config.get("REDDIT_CLIENT_SECRET")

        # client_id/secret alone give read-only app access, which is all these
        # commands need. Username/password are optional and only used when set.
        if client_id and client_secret:
            credentials = {
                "client_id": client_id,
                "client_secret": client_secret,
                "user_agent": env_config.get("REDDIT_USER_AGENT", "AiriBot/1.0"),
                "check_for_async": False,
            }
            username = env_config.get("REDDIT_USERNAME")
            password = env_config.get("REDDIT_PASSWORD")
            if username and password:
                credentials.update(username=username, password=password)
            try:
                self.reddit = praw.Reddit(**credentials)
            except Exception as e:
                print(f"Reddit init failed: {e}")
        else:
            print("Reddit commands disabled: REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set in .env")

        self.r_store = []

    async def ensure_reddit(self, ctx):
        """Reply with a setup hint and return False when Reddit isn't configured."""
        if self.reddit is not None:
            return True
        await ctx.send(embed=discord.Embed(
            description="Reddit commands are not configured. Set `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` in `.env` (create a **script** app at https://www.reddit.com/prefs/apps).",
            color=discord.Color.red()))
        return False

    def _fetch_posts(self, subreddit_name, sort='hot', limit=50):
        """Sync PRAW call — iterating the listing fires the HTTP request. Run via
        asyncio.to_thread, never called directly from a coroutine."""
        subreddit = self.reddit.subreddit(subreddit_name)
        listing = subreddit.top(limit=limit) if sort == 'top' else subreddit.hot(limit=limit)
        return list(listing)

    @staticmethod
    def _is_media_post(submission):
        url = getattr(submission, 'url', '')
        return url.endswith(('.jpg', '.png', '.gif', '.jpeg', '.gifv')) or 'redgifs.com' in url.split('/')

    async def r_recursive(self, ctx, img_arr):
        # Filter once instead of recursing into the same already-fetched list —
        # re-picking randomly from a list with no matching posts just spun on
        # the same data without ever making a new request.
        media_posts = [img for img in img_arr if self._is_media_post(img)]
        if not media_posts:
            return await ctx.send(embed=discord.Embed(description="Can't find any image post, Try Again !", color=discord.Color.red()))
        random_img = random.choice(media_posts)
        url = random_img.url
        title = getattr(random_img, 'title', '')
        if url.endswith(('.jpg', '.png', '.gif', '.jpeg')):
            embed = discord.Embed()
            embed.set_image(url=url)
            embed.set_footer(
                text=f"♥ {getattr(random_img, 'score', 0)} | {title[:40] if len(title)>40 else title}")
            await ctx.send(embed=embed)
        else:
            await ctx.send(url)

    @commands.command(aliases=['nsfw'])
    async def nsfw_reddit(self, ctx, arg=None):
        if not await self.ensure_reddit(ctx):
            return
        if ctx.message.channel.is_nsfw():

            # Don't Judge me, I don't follow these subreddits (Just googled them)

            non_anime = ['60fpsporn', 'NSFW_GIF', 'NSFW_GIFS']
            anime = ['rule34', 'Hentai']
            print(arg)
            if arg == None:
                img_arr = await asyncio.to_thread(self._fetch_posts, random.choice(non_anime))
            else:
                img_arr = await asyncio.to_thread(self._fetch_posts, random.choice(anime))
            await self.r_recursive(ctx, img_arr)
        else:
            await ctx.send(embed=discord.Embed(description="Warning : It's not a NSFW text channel !",color=discord.Color.red()))

    @commands.hybrid_command(name="meme", aliases=['memes'], description="Get a random meme from Reddit")
    async def meme_reddit(self,ctx):
        if not await self.ensure_reddit(ctx):
            return
        popular = ['memes','meme','ProgrammerHumor','dankmemes']
        img_arr = await asyncio.to_thread(self._fetch_posts, random.choice(popular))
        await self.r_recursive(ctx, img_arr)

    @commands.hybrid_command(name="reddit", aliases=['r'], description="Fetch top posts from a subreddit")
    @app_commands.describe(subreddit="Subreddit name or shortcut (e.g. anime, wall, prog, memes)")
    async def r_subreddit(self, ctx, subreddit: str = None):
        if not await self.ensure_reddit(ctx):
            return
        popular = {'wall': 'wallpaper', "memes": 'memes', "meme": 'dankmemes', 'prog': 'ProgrammerHumor',
                   'anime': 'Anime', 'awall': 'Animewallpaper', 'ep': 'earthporn', 'sp': 'spaceporn'}
        ul = "https://www.reddit.com/r/"
        message = subreddit
        if message == None:
            embed = discord.Embed(
                title="Suggestions", description="Try the following arguments :", color=discord.Color.orange())
            embed.add_field(
                name="__Args__", value="> wall\n> memes\n> prog\n> anime\n> awall\n> ep\n> sp", inline=True)
            embed.add_field(
                name="__Redirects__", value=f"[Wallpaper]({ul}wallpaper)\n[Memes]({ul}memes)\n[ProgrammerHumor]({ul}ProgrammerHumor)\n[Anime]({ul}Anime)\n[Animewallpapers]({ul}AnimeWallpapers)\n[EarthPorn]({ul}earthporn)\n[SpacePorn]({ul}spaceporn)", inline=True)
            embed.set_footer(text="use command /reddit <Arg> or .r <Arg>")
            await ctx.send(embed=embed)
        else:
            try:
                if message in popular:
                    message = popular[message]
                img_arr = await asyncio.to_thread(self._fetch_posts, message, 'top')

                random_img = random.choice(img_arr)
                embed = discord.Embed()
                embed.set_image(url=random_img.url)
                embed.set_footer(
                    text=f"♥ {random_img.score} | From r/{message}")
                em = await ctx.send(embed=embed)
                if not ctx.interaction:
                    try:
                        await em.add_reaction("♥")
                    except Exception:
                        pass
            except IndexError:
                await ctx.send(embed=discord.Embed(description="Posts not found, Try Again !", color=discord.Color.red()))
            except Exception as e:
                # Log it: auth failures and rate limits used to be reported as
                # "subreddit does not exist", which hid the real problem.
                print(f"Reddit error for r/{message}: {type(e).__name__}: {e}")
                await ctx.send(embed=discord.Embed(description="Subreddit does not exist, or Reddit rejected the request (check credentials / rate limit).", color=discord.Color.red()))

async def setup(client):
    await client.add_cog(Reddit(client))
