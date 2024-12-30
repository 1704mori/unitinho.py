#!/usr/bin/env python
import os

import discord
from discord.ext import commands

intents = discord.Intents.all()

cogs: list = ["cogs.music.music"]

client = commands.Bot(command_prefix=os.getenv("BOT_PREFIX"), help_command=None, intents=intents)

@client.event
async def on_ready():
    await client.change_presence(status=discord.Status.online, activity=discord.Game('prefix: .'))

    for cog in cogs:
        try:
            print(f"Loading cog {cog}")
            await client.load_extension(cog)
        except Exception as e:
            exc = "{}: {}".format(type(e).__name__, e)
            print("Failed to load cog {}\n{}".format(cog, exc))
        else:
            print(f"Loaded cog {cog}")
    
    print("bot is ready")

@client.command()
@commands.has_permissions(manage_guild=True)
async def sync(ctx: commands.Context):
    synced = await ctx.bot.tree.sync()
    await ctx.send(f'Command tree synced {len(synced)} commands.')

client.run(os.getenv("BOT_TOKEN"))