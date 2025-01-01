import asyncio
import functools
import math
import random
import aiohttp
import traceback
import subprocess
import json

import discord
from discord.ext import commands
from discord import app_commands

from .ytdl import YTDLError, YTDLSource, FFMPEG_OPTIONS
from .queue import SongQueue

from typing import Optional

class Song:
    __slots__ = ('source', 'requester')

    def __init__(self, source: YTDLSource):
        self.source = source
        self.requester = source.requester

    def create_embed(self):
        embed = (discord.Embed(title='Now playing',
                               description='```css\n{0.source.title}\n```'.format(self),
                               color=discord.Color.blurple())
                 .add_field(name='Duration', value=self.source.duration)
                 .add_field(name='Requested by', value=self.requester.mention)
                 .add_field(name='Uploader', value='[{0.source.uploader}]({0.source.uploader_url})'.format(self))
                 .add_field(name='URL', value='[Click]({0.source.url})'.format(self))
                 .set_thumbnail(url=self.source.thumbnail))

        return embed

class VoiceState:
    def __init__(self, bot: commands.Bot, ctx: commands.Context):
        self.bot = bot
        self._ctx = ctx

        self.current = None
        self.voice = None
        self.next = asyncio.Event()
        self.songs = SongQueue()

        self._loop = False
        self._volume = 0.5
        self.skip_votes = set()

        self.audio_player = bot.loop.create_task(self.audio_player_task())

    def __del__(self):
        self.audio_player.cancel()

    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, value: bool):
        self._loop = value

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = value

    @property
    def is_playing(self):
        return self.voice and self.current

    async def audio_player_task(self):
        while True:
            self.next.clear()

            if not self.loop:
                # Try to get the next song within 3 minutes.
                # If no song will be added to the queue in time,
                # the player will disconnect due to performance
                # reasons.
                try:
                    async with asyncio.timeout(180):  # 3 minutes
                        self.current = await self.songs.get()
                except asyncio.TimeoutError:
                    self.bot.loop.create_task(self.stop())
                    return

            self.current.source.volume = self._volume
            self.voice.play(self.current.source, after=self.play_next_song)
            await self.current.source.channel.send(embed=self.current.create_embed())

            await self.next.wait()

    def play_next_song(self, error=None):
        if error:
            raise VoiceError(str(error))

        self.next.set()

    def skip(self):
        self.skip_votes.clear()

        if self.is_playing:
            self.voice.stop()

    async def stop(self):
        self.songs.clear()

        if self.voice:
            await self.voice.disconnect()
            self.voice = None

class MusicContext(commands.Context):
    voice_state: Optional[VoiceState]

class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_states = {}
        

    def get_voice_state(self, ctx: MusicContext):
        state = self.voice_states.get(ctx.guild.id)
        if not state:
            state = VoiceState(self.bot, ctx)
            self.voice_states[ctx.guild.id] = state

        return state

    def cog_unload(self):
        for state in self.voice_states.values():
            self.bot.loop.create_task(state.stop())

    def cog_check(self, ctx: MusicContext):
        if not ctx.guild:
            raise commands.NoPrivateMessage('This command can\'t be used in DM channels.')

        return True

    async def cog_before_invoke(self, ctx: MusicContext):
        ctx.voice_state = self.get_voice_state(ctx)

    async def cog_command_error(self, ctx: MusicContext, error: commands.CommandError):
        traceback.print_exc(error)
        await ctx.send('An error occurred: {}'.format(str(error)))

    async def is_audio_url(self, url: str):
        """Checks if the provided URL points to an audio file."""
        async with aiohttp.ClientSession() as session:
            async with session.head(url) as response:
                content_type = response.headers.get('Content-Type', '')
                return content_type.startswith('audio/')

    async def create_audio_source(self, ctx: MusicContext, url: str):
        """Handles generic audio URLs."""
        async with aiohttp.ClientSession() as session:
            async with session.head(url) as response:
                content_type = response.headers.get('Content-Type', '')
                title = url.split('/')[-1]  # Use the filename or fallback as the title.

        return self(ctx, discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), data={
            'title': title,
            'url': url,
            'uploader': 'Direct Audio URL',
            'uploader_url': None,
            'thumbnail': None,
            'duration': 0,  # Unknown for direct audio URLs
            'views': None,
            'like_count': None,
            'dislike_count': None,
        })

    @commands.hybrid_command(name='join', invoke_without_subcommand=True)
    async def _join(self, ctx: MusicContext):
        """Joins a voice channel."""

        destination = ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.hybrid_command(name='summon')
    @commands.has_permissions(manage_guild=True)
    async def _summon(self, ctx: MusicContext, *, channel: discord.VoiceChannel = None):
        """Summons the bot to a voice channel.

        If no channel was specified, it joins your channel.
        """

        if not channel and not ctx.author.voice:
            raise VoiceError('You are neither connected to a voice channel nor specified a channel to join.')

        destination = channel or ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name='leave', aliases=['disconnect'])
    @commands.has_permissions(manage_guild=True)
    async def _leave(self, ctx: MusicContext):
        """Clears the queue and leaves the voice channel."""

        if not ctx.voice_state.voice:
            return await ctx.send('Not connected to any voice channel.')

        await ctx.voice_state.stop()
        del self.voice_states[ctx.guild.id]

    @commands.hybrid_command(name='volume')
    async def _volume(self, ctx: MusicContext, *, volume: int):
        """Sets the volume of the player."""

        if not ctx.voice_state.is_playing:
            return await ctx.send('Nothing being played at the moment.')

        if 0 > volume > 100:
            return await ctx.send('Volume must be between 0 and 100')

        ctx.voice_state.volume = volume / 100
        await ctx.send('Volume of the player set to {}%'.format(volume))

    @commands.hybrid_command(name='now', aliases=['current', 'playing'])
    async def _now(self, ctx: MusicContext):
        """Displays the currently playing song."""

        await ctx.send(embed=ctx.voice_state.current.create_embed())

    @commands.hybrid_command(name='pause')
    @commands.has_permissions(manage_guild=True)
    async def _pause(self, ctx: MusicContext):
        """Pauses the currently playing song."""

        if not ctx.voice_state.is_playing and ctx.voice_state.voice.is_playing():
            ctx.voice_state.voice.pause()
            await ctx.message.add_reaction('⏯')

    @commands.hybrid_command(name='resume')
    @commands.has_permissions(manage_guild=True)
    async def _resume(self, ctx: MusicContext):
        """Resumes a currently paused song."""

        if not ctx.voice_state.is_playing and ctx.voice_state.voice.is_paused():
            ctx.voice_state.voice.resume()
            await ctx.message.add_reaction('⏯')

    @commands.hybrid_command(name='stop')
    @commands.has_permissions(manage_guild=True)
    async def _stop(self, ctx: MusicContext):
        """Stops playing song and clears the queue."""

        ctx.voice_state.songs.clear()

        if not ctx.voice_state.is_playing:
            ctx.voice_state.voice.stop()
            await ctx.message.add_reaction('⏹')

    @commands.hybrid_command(name='skip')
    async def _skip(self, ctx: MusicContext):
        """Vote to skip a song. The requester can automatically skip.
        3 skip votes are needed for the song to be skipped.
        """

        if not ctx.voice_state.is_playing:
            return await ctx.send('Not playing any music right now...')

        voter = ctx.message.author
        if voter == ctx.voice_state.current.requester:
            await ctx.message.add_reaction('⏭')
            ctx.voice_state.skip()

        elif voter.id not in ctx.voice_state.skip_votes:
            ctx.voice_state.skip_votes.add(voter.id)
            total_votes = len(ctx.voice_state.skip_votes)

            if total_votes >= 3:
                await ctx.message.add_reaction('⏭')
                ctx.voice_state.skip()
            else:
                await ctx.send('Skip vote added, currently at **{}/3**'.format(total_votes))

        else:
            await ctx.send('You have already voted to skip this song.')

    @commands.hybrid_command(name='queue')
    async def _queue(self, ctx: MusicContext, *, page: int = 1):
        """Shows the player's queue.

        You can optionally specify the page to show. Each page contains 10 elements.
        """

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('Empty queue.')

        items_per_page = 10
        pages = math.ceil(len(ctx.voice_state.songs) / items_per_page)

        start = (page - 1) * items_per_page
        end = start + items_per_page

        queue = ''
        for i, song in enumerate(ctx.voice_state.songs[start:end], start=start):
            queue += '`{0}.` [**{1.source.title}**]({1.source.url})\n'.format(i + 1, song)

        embed = (discord.Embed(description='**{} tracks:**\n\n{}'.format(len(ctx.voice_state.songs), queue))
                 .set_footer(text='Viewing page {}/{}'.format(page, pages)))
        await ctx.send(embed=embed)

    @commands.hybrid_command(name='shuffle')
    async def _shuffle(self, ctx: MusicContext):
        """Shuffles the queue."""

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('Empty queue.')

        ctx.voice_state.songs.shuffle()
        await ctx.message.add_reaction('✅')

    @commands.hybrid_command(name='remove')
    async def _remove(self, ctx: MusicContext, index: int):
        """Removes a song from the queue at a given index."""

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('Empty queue.')

        ctx.voice_state.songs.remove(index - 1)
        await ctx.message.add_reaction('✅')

    @commands.hybrid_command(name='loop')
    async def _loop(self, ctx: MusicContext):
        """Loops the currently playing song.

        Invoke this command again to unloop the song.
        """

        if not ctx.voice_state.is_playing:
            return await ctx.send('Nothing being played at the moment.')

        # Inverse boolean value to loop and unloop.
        ctx.voice_state.loop = not ctx.voice_state.loop
        await ctx.message.add_reaction('✅')

    async def get_metadata_with_ffprobe(self, url: str):
        """
        Uses ffprobe to extract metadata from a direct audio URL.
        """
        command = [
            'ffprobe', '-hide_banner', '-loglevel', 'error',
            '-print_format', 'json',
            '-show_format', '-show_streams', url
        ]
        try:
            # Run ffprobe as a subprocess and capture the output
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            metadata = json.loads(result.stdout)
            return metadata
        except Exception as e:
            print(f"Error extracting metadata with ffprobe: {e}")
            return {}

    async def create_audio_source(self, ctx: MusicContext, url: str):
        """Handles generic audio URLs with metadata extraction."""
        async with aiohttp.ClientSession() as session:
            async with session.head(url) as response:
                content_type = response.headers.get('Content-Type', '')
                title = url.split('/')[-1]  # Use the filename or fallback as the title.

                # Extract additional metadata
                metadata = await self.get_metadata_with_ffprobe(url)
                duration = 0  # Default duration
                if 'format' in metadata and 'duration' in metadata['format']:
                    duration = int(float(metadata['format']['duration']))  # Convert to seconds if available

                return YTDLSource(ctx, discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), data={
                    'title': title,
                    'url': url,
                    'uploader': 'Direct Audio URL',
                    'uploader_url': None,
                    'thumbnail': 'http://example.com',
                    'upload_date': 'None',
                    'duration': duration,
                    'views': None,
                    'like_count': None,
                    'dislike_count': None,
                })

    @commands.hybrid_command(name='play')
    async def _play(self, ctx: MusicContext, *, search: str):
        """Plays a song.

        If there are songs in the queue, this will be queued until the
        other songs finished playing.
        """

        if not ctx.voice_state.voice:
            await ctx.invoke(self._join)

        async with ctx.typing():
            try:
                if 'spotify.com' in search:
                    source = await YTDLSource.handle_spotify_url(ctx, search)
                elif await self.is_audio_url(search):
                    source = await self.create_audio_source(ctx, search)
                else:
                    source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop)
            except YTDLError as e:
                await ctx.send('An error occurred while processing this request: {}'.format(str(e)))
            else:
                song = Song(source)
                await ctx.voice_state.songs.put(song)
                await ctx.send('Enqueued {}'.format(str(source)))

    @_join.before_invoke
    @_play.before_invoke
    async def ensure_voice_state(self, ctx: MusicContext):
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError('You are not connected to any voice channel.')

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                raise commands.CommandError('Bot is already in a voice channel.')

async def setup(bot: commands.bot.Bot):
    await bot.add_cog(Music(bot))