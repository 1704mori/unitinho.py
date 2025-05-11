#!/usr/bin/env python
import os

import discord
from discord.ext import commands

intents = discord.Intents.all()

cogs: list = ["cogs.music.music"]

client = commands.Bot(command_prefix=os.getenv("BOT_PREFIX"), help_command=None, intents=intents)
client.remove_command('help')

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


@client.command()
async def help(ctx, args=None):
    help_embed = discord.Embed(title=f"{os.getenv("BOT_NAME")}'s Help!")
    command_names_list = [x.name for x in client.commands]

    if not args:
        help_embed.add_field(
            name="List of supported commands:",
            value="\n".join([str(i+1)+". "+x.name for i,x in enumerate(client.commands)]),
            inline=False
        )
        help_embed.add_field(
            name="Details",
            value="Type `.help <command name>` for more details about each command.",
            inline=False
        )

    elif args in command_names_list:
        help_embed.add_field(
            name=args,
            value=client.get_command(args).help
        )

    else:
        help_embed.add_field(
            name="Nope.",
            value="Don't think I got that command, boss!"
        )

    await ctx.send(embed=help_embed)

client.run(os.getenv("BOT_TOKEN"))
