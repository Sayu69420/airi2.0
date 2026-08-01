import discord
import asyncio
from discord.ext import commands

import env_config

class Mod(commands.Cog):
    def __init__(self,client):
        self.client = client
        self.mute = []
        self.afk = []
        self.poll_ids = []

    @commands.has_permissions(manage_messages=True)
    @commands.command(aliases=['mute'])
    async def _mute(self, ctx, member: discord.Member, time: int = None):
        await ctx.message.add_reaction("👌")
        guild = ctx.guild
        muted_id = member.id
        if muted_id not in self.mute:
            self.mute.append(muted_id)
        role = discord.utils.get(guild.roles, name='Mute')
        if role is None:
            print('creating mute role')
            role = await guild.create_role(name='Mute', permissions=discord.Permissions(send_messages=False))
            await ctx.channel.set_permissions(role, read_messages=True, send_messages=False)
        else:
            await ctx.channel.set_permissions(role, read_messages=True, send_messages=False)

        await member.add_roles(role)

        embed = discord.Embed(description=f'{member.name} has been muted' + (f' for {time} minutes.' if time else '.'), color=discord.Color.red())
        await ctx.send(embed=embed)

        if time is not None:
            await asyncio.sleep(time * 60)
            if role in member.roles:
                await member.remove_roles(role)
            if muted_id in self.mute:
                self.mute.remove(muted_id)
            embed = discord.Embed(description=f'{member.name} is free to speak now.', color=discord.Color.green())
            await ctx.send(embed=embed)

    @commands.has_permissions(manage_messages=True)
    @commands.command(aliases=['unmute'])
    async def _unmute(self, ctx, member: discord.Member):
        await ctx.message.add_reaction("👌")
        guild = ctx.guild
        muted_id = member.id
        role = discord.utils.get(guild.roles, name='Mute')
        if role and role in member.roles:
            await member.remove_roles(role)
            if muted_id in self.mute:
                self.mute.remove(muted_id)
            embed = discord.Embed(description=f'{member.name} is free to speak now.', color=discord.Color.green())
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(description=f'{member.name} was not muted.', color=discord.Color.green())
            await ctx.send(embed=embed)

    @commands.command(aliases=['ping'])
    async def ping_(self,ctx):
        embed = discord.Embed(description=f'pong ! {str(round(self.client.latency*1000))}ms',color=discord.Color.blurple())
        await ctx.reply(embed=embed)

    @commands.has_permissions(manage_messages=True)
    @commands.command(aliases=['purge','del','delete'])
    async def delete_(self,ctx,num=1):
        await ctx.channel.purge(limit = num+1)

    @commands.command(aliases=['afk'])
    async def afk_(self, ctx, *, reason='Not Given'):
        user_id = ctx.author.id
        if user_id in [u.id for u in self.afk]:
            self.afk = [u for u in self.afk if u.id != user_id]
            embed = discord.Embed(description="You are set to online now !", color=discord.Color.green())
            await ctx.reply(embed=embed)
        else:
            self.afk.append(ctx.author)
            embed = discord.Embed(description=f"{ctx.author.name}, You are set to AFK now! Reason: `{reason}`", color=discord.Color.red())
            await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        # Check if author is AFK and remove AFK status
        afk_user_ids = [u.id for u in self.afk]
        if message.author.id in afk_user_ids:
            self.afk = [u for u in self.afk if u.id != message.author.id]
            embed = discord.Embed(description=f"Welcome back {message.author.mention}, I removed your AFK status!", color=discord.Color.green())
            await message.channel.send(embed=embed)

        # Check if any mentioned users are AFK
        for member in message.mentions:
            if member.id in afk_user_ids:
                embed = discord.Embed(description=f"**{member.name}** is currently AFK!", color=discord.Color.red())
                await message.channel.send(embed=embed)

        # Check if bot was mentioned
        if self.client.user in message.mentions:
            embed = discord.Embed(description="My prefix is `.`", color=discord.Color.blurple())
            await message.channel.send(embed=embed)

    @commands.command(aliases=['join'])
    async def join_(self,ctx,member:discord.Member=None):
        try:
            channel = ctx.author.voice.channel.id
            if member != None:
                await ctx.send(f'{member.mention}')
            else:
                await ctx.send(f'{ctx.message.guild.default_role}')
            await ctx.send(embed=discord.Embed(description=f"📢 Join <#{channel}>",color=discord.Color.orange()))
        except AttributeError as e:
            print(f"join_ error: {type(e).__name__}: {e}")
            await ctx.send(embed=discord.Embed(description="You are not connected to any VoiceChannel",color=discord.Color.red()))
        # embed = discord.Embed(description="use `join vc <@person>` or `join tc <@person>`")

    @commands.command(aliases=['invite'])
    async def create_invite(self, ctx,age=5):
        link = await ctx.channel.create_invite(max_age=age*60)
        await ctx.send(f"**Timeout: {age} Minutes** {link}")

    @commands.command(aliases=['bug','report'])
    async def bug_report(self,ctx,*,arg):
        channel_id = env_config.get_id('BUG_REPORT_CHANNEL_ID')
        bug_channel = self.client.get_channel(channel_id) if channel_id else None
        if not bug_channel:
            return await ctx.send(embed=discord.Embed(description="Bug report channel is not configured correctly.", color=discord.Color.red()))
        try:
            url = ctx.message.attachments[0].url
        except IndexError:
            url = None
        await bug_channel.send(embed=discord.Embed(title="Bug Report",description=f"{arg} : {ctx.author.mention} in {ctx.guild.name}",color=discord.Color.red()))
        if url != None:
            await bug_channel.send(url)
        msg = await ctx.send(embed=discord.Embed(description="Bug Report sent !!",color=discord.Color.orange()))
        await asyncio.sleep(10)
        await msg.delete()
    # @commands.command()
    # async def poll(self,ctx,*,msg):
    #     conn = sqlite3.connect('fav_disc.db')
    #     c = conn.cursor()
    #     try:
    #         c.execute("""CREATE TABLE poll(
    #                 id integer,
    #                 poll text
    #                 )""")
    #     except:
    #         print("db already there")
    #     msg = msg.split(':')
    #     title = msg[0]
    #     desc = msg[1]
    #     values = msg[2]
    #     values = values.split(',')
    #     embed = discord.Embed(title=title,description=desc,color=discord.Color.orange())
    #     for i in range(len(values)):
    #         embed.add_field(name='')
    # >>>> MAYBE I'LL IMPLEMENT IT LATER

async def setup(client):
    await client.add_cog(Mod(client))
