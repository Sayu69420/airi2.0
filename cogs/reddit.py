import discord, praw, random, os
from praw.reddit import Subreddit
from discord.ext import commands

class Reddit(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.reddit = praw.Reddit(
            client_id=os.getenv("REDDIT_CLIENT_ID", "REDDIT_ID"),
            client_secret=os.getenv("REDDIT_CLIENT_SECRET", "REDDIT_SECRET"),
            username=os.getenv("REDDIT_USERNAME", "REDDIT_USER"),
            password=os.getenv("REDDIT_PASSWORD", "REDDIT_PASS"),
            user_agent=os.getenv("REDDIT_USER_AGENT", "AiriBot/1.0"),
            check_for_async=False
        )
        self.r_store = []

    async def r_recursive(self, ctx, img_arr, depth=0):
        if not img_arr or depth > 15:
            return await ctx.send(embed=discord.Embed(description="Can't find any image post, Try Again !", color=discord.Color.red()))
        random_img = random.choice(img_arr)
        url = getattr(random_img, 'url', '')
        title = getattr(random_img, 'title', '')
        if url.endswith(('.jpg', '.png', '.gif', '.jpeg')):
            embed = discord.Embed()
            embed.set_image(url=url)
            embed.set_footer(
                text=f"♥ {getattr(random_img, 'score', 0)} | {title[:40] if len(title)>40 else title}")
            await ctx.send(embed=embed)
        elif url.endswith('.gifv') or 'redgifs.com' in url.split("/"):
            await ctx.send(url)
        else:
            await self.r_recursive(ctx, img_arr, depth + 1)

    @commands.command(aliases=['nsfw'])
    async def nsfw_reddit(self, ctx, arg=None):
        if ctx.message.channel.is_nsfw():

            # Don't Judge me, I don't follow these subreddits (Just googled them)

            non_anime = ['60fpsporn', 'NSFW_GIF', 'NSFW_GIFS']
            anime = ['rule34', 'Hentai']
            print(arg)
            if arg == None:
                subreddit = self.reddit.subreddit(random.choice(non_anime))
                new = subreddit.hot(limit=50)
                img_arr = []
                for img in new:
                    img_arr.append(img)
                try:
                    await self.r_recursive(ctx, img_arr)
                except RecursionError:
                    await ctx.send(embed=discord.Embed(description="Can't find any post, Try Again !"))
            else:
                subreddit = self.reddit.subreddit(random.choice(anime))
                new = subreddit.hot(limit=50)
                img_arr = []
                for img in new:
                    img_arr.append(img)
                try:
                    await self.r_recursive(ctx, img_arr)
                except RecursionError:
                    await ctx.send(embed=discord.Embed(description="Can't find any post, Try Again !"))
        else:
            await ctx.send(embed=discord.Embed(description="Warning : It's not a NSFW text channel !",color=discord.Color.red()))

    @commands.command(aliases=['memes','meme'])
    async def meme_reddit(self,ctx):
        popular = ['memes','meme','ProgrammerHumor','dankmemes']
        subreddit = self.reddit.subreddit(random.choice(popular))
        new = subreddit.hot(limit=50)
        img_arr = []
        for img in new:
            img_arr.append(img)
        try:
            await self.r_recursive(ctx,img_arr)
        except RecursionError:
            await ctx.send(embed=discord.Embed(description="Can't find any post, Try Again !"))

    @commands.command(aliases=['r'])
    async def r_subreddit(self, ctx, message=None):
        popular = {'wall': 'wallpaper', "memes": 'memes', "meme": 'dankmemes', 'prog': 'ProgrammerHumor',
                   'anime': 'Anime', 'awall': 'Animewallpaper', 'ep': 'earthporn', 'sp': 'spaceporn'}
        ul = "https://www.reddit.com/r/"
        if message == None:
            embed = discord.Embed(
                title="Suggestions", description="Try the following arguments :", color=discord.Color.orange())
            embed.add_field(
                name="__Args__", value="> wall\n> memes\n> prog\n> anime\n> awall\n> ep\n> sp", inline=True)
            embed.add_field(
                name="__Redirects__", value=f"[Wallpaper]({ul}wallpaper)\n[Memes]({ul}memes)\n[ProgrammerHumor]({ul}ProgrammerHumor)\n[Anime]({ul}Anime)\n[Animewallpapers]({ul}AnimeWallpapers)\n[EarthPorn]({ul}earthporn)\n[SpacePorn]({ul}spaceporn)", inline=True)
            embed.set_footer(text="use command ,r/ <Arg>")
            await ctx.send(embed=embed)
        else:
            try:
                if message in popular:
                    message = popular[message]
                subreddit = self.reddit.subreddit(message)
                new = subreddit.top(limit=50)
                img_arr = []
                for img in new:
                    img_arr.append(img)


                random_img = random.choice(img_arr)
                embed = discord.Embed()
                embed.set_image(url=random_img.url)
                embed.set_footer(
                    text=f"♥ {random_img.score} | From r/{message}")
                em = await ctx.send(embed=embed)
                await em.add_reaction("♥")
            except IndexError:
                await ctx.send(embed=discord.Embed(description="Posts not found, Try Again !", color=discord.Color.red()))
            except:
                await ctx.send(embed=discord.Embed(description="Subreddit does not exist.", color=discord.Color.red()))

async def setup(client):
    await client.add_cog(Reddit(client))
