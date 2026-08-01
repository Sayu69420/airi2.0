import discord
from discord.ext import commands
import sqlite3

import env_config

class Config(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Schema lives in env_config.SCHEMA and is applied at startup.
        self.db = sqlite3.connect(env_config.DB_PATH)
        self.cursor = self.db.cursor()

    @commands.command(name="prefix")
    async def _prefix(self, ctx, prefix: str = None):
        if not ctx.guild:
            return await ctx.send("This command can only be used in a server.")

        if prefix is None:
            self.cursor.execute("SELECT prefix FROM config WHERE guild_id = ?", (ctx.guild.id,))
            y = self.cursor.fetchone()
            if y is None:
                em = discord.Embed(title="My Prefixes Here", description="`1.` **.**", color=discord.Color.blurple())
            else:
                em = discord.Embed(title="My Prefixes Here", description=f"`1.` **.**\n`2.` **{y[0]}**", color=discord.Color.blurple())
            await ctx.send(embed=em)
        else:
            self.cursor.execute("INSERT OR REPLACE INTO config (guild_id, prefix) VALUES (?, ?)", (ctx.guild.id, prefix))
            self.db.commit()
            em = discord.Embed(title="Prefix Updated", description=f"Your Server Prefix Updated to : `{prefix}`", color=discord.Color.green())
            await ctx.send(embed=em)

async def setup(bot):
    await bot.add_cog(Config(bot))