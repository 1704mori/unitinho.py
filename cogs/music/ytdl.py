import asyncio
import functools
import re
from html.parser import HTMLParser

import aiohttp
import discord
import yt_dlp
from discord.ext import commands


class YTDLError(Exception):
    pass

YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.metadata = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'meta':
            attrs = dict(attrs)
            if 'content' in attrs:
                if 'property' in attrs:
                    self.metadata[attrs['property']] = attrs['content']
                elif 'name' in attrs:
                    self.metadata[attrs['name']] = attrs['content']

class YTDLSource(discord.PCMVolumeTransformer):
    ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

    def __init__(self, ctx: commands.Context, source: discord.FFmpegPCMAudio, *, data: dict, volume: float = 0.5):
        super().__init__(source, volume)

        self.requester = ctx.author
        self.channel = ctx.channel
        self.data = data

        self.uploader = data.get('uploader')
        self.uploader_url = data.get('uploader_url')
        date = data.get('upload_date')
        self.upload_date = date[6:8] + '.' + date[4:6] + '.' + date[0:4]
        self.title = data.get('title')
        self.thumbnail = data.get('thumbnail')
        self.description = data.get('description')
        self.duration = self.parse_duration(int(data.get('duration')))
        self.tags = data.get('tags')
        self.url = data.get('webpage_url')
        self.views = data.get('view_count')
        self.likes = data.get('like_count')
        self.dislikes = data.get('dislike_count')
        self.stream_url = data.get('url')

    def __str__(self):
        return '**{0.title}** by **{0.uploader}**'.format(self)

    @classmethod
    async def create_source(cls, ctx: commands.Context, search: str, *, loop: asyncio.BaseEventLoop = None):
        loop = loop or asyncio.get_event_loop()

        partial = functools.partial(cls.ytdl.extract_info, search, download=False, process=False)
        data = await loop.run_in_executor(None, partial)

        if data is None:
            raise YTDLError('Couldn\'t find anything that matches `{}`'.format(search))

        if 'entries' not in data:
            process_info = data
        else:
            process_info = None
            for entry in data['entries']:
                if entry:
                    process_info = entry
                    break

            if process_info is None:
                raise YTDLError('Couldn\'t find anything that matches `{}`'.format(search))

        webpage_url = process_info['webpage_url']
        partial = functools.partial(cls.ytdl.extract_info, webpage_url, download=False)
        processed_info = await loop.run_in_executor(None, partial)

        if processed_info is None:
            raise YTDLError('Couldn\'t fetch `{}`'.format(webpage_url))

        if 'entries' not in processed_info:
            info = processed_info
        else:
            info = None
            while info is None:
                try:
                    info = processed_info['entries'].pop(0)
                except IndexError:
                    raise YTDLError('Couldn\'t retrieve any matches for `{}`'.format(webpage_url))

        return cls(ctx, discord.FFmpegPCMAudio(info['url'], **FFMPEG_OPTIONS), data=info)

    @classmethod
    async def handle_spotify_url(cls, ctx: commands.Context, url: str):
        """Handles Spotify URLs by extracting metadata and finding the best match on YouTube."""
        track_info = await cls.get_spotify_metadata(url)
        
        if not track_info:
            raise YTDLError('Could not extract track information from Spotify URL')

        search_query = f"{track_info['artist']} - {track_info['title']}"
        return await cls.search_best_match(ctx, search_query, track_info)

    @staticmethod
    async def get_spotify_metadata(url: str) -> dict:
        """Extracts metadata from Spotify URL using HTMLParser."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as response:
                    if response.status != 200:
                        raise YTDLError(f'Failed to fetch Spotify page: {response.status}')
                    
                    html = await response.text()
                    parser = MetaParser()
                    parser.feed(html)
                    
                    metadata = {}
                    meta_tags = parser.metadata
                    
                    if 'og:title' in meta_tags:
                        metadata['title'] = meta_tags['og:title']
                    
                    if 'og:description' in meta_tags:
                        metadata['description'] = meta_tags['og:description']
                    
                    if 'og:image' in meta_tags:
                        metadata['image'] = meta_tags['og:image']
                    
                    if 'music:musician_description' in meta_tags:
                        metadata['artist'] = meta_tags['music:musician_description']

                    # If artist not found in musician tag, try to extract from title
                    if 'artist' not in metadata and ' - ' in metadata.get('title', ''):
                        metadata['artist'] = metadata['title'].split(' - ')[0].strip()
                        metadata['title'] = metadata['title'].split(' - ')[1].strip()
                    
                    parser.close()
                    return metadata
                    
            except Exception as e:
                raise YTDLError(f'Error extracting Spotify metadata: {str(e)}')

    @classmethod
    async def search_best_match(cls, ctx: commands.Context, search_query: str, spotify_info: dict):
        """Searches for the best matching video on YouTube and creates a source."""
        loop = ctx.bot.loop or asyncio.get_event_loop()
        
        # First, search for videos
        partial = functools.partial(cls.ytdl.extract_info, f"ytsearch5:{search_query}", download=False)
        info = await loop.run_in_executor(None, partial)
        
        if not info or 'entries' not in info:
            raise YTDLError(f'Could not find matches for `{search_query}`')
        
        # Score and find the best match
        best_match = None
        best_score = float('-inf')
        
        for entry in info['entries']:
            if not entry:
                continue
                
            score = cls.calculate_match_score(entry, spotify_info)
            if score > best_score:
                best_score = score
                best_match = entry
        
        if not best_match:
            raise YTDLError(f'No suitable matches found for `{search_query}`')

        # Get the full info for the best match
        webpage_url = best_match['webpage_url']
        partial = functools.partial(cls.ytdl.extract_info, webpage_url, download=False)
        processed_info = await loop.run_in_executor(None, partial)

        if processed_info is None:
            raise YTDLError(f'Couldn\'t fetch `{webpage_url}`')

        if 'entries' not in processed_info:
            info = processed_info
        else:
            info = None
            while info is None:
                try:
                    info = processed_info['entries'].pop(0)
                except IndexError:
                    raise YTDLError(f'Couldn\'t retrieve any matches for `{webpage_url}`')

        return cls(ctx, discord.FFmpegPCMAudio(info['url'], **FFMPEG_OPTIONS), data=info)

    @staticmethod
    def calculate_match_score(yt_entry: dict, spotify_info: dict) -> float:
        """Calculates a matching score between YouTube entry and Spotify metadata."""
        score = 0.0
        
        def clean_text(text):
            return re.sub(r'[^\w\s]', '', text.lower())
        
        yt_title = clean_text(yt_entry.get('title', ''))
        spotify_title = clean_text(spotify_info.get('title', ''))
        spotify_artist = clean_text(spotify_info.get('artist', ''))
        
        # Title match
        if spotify_title in yt_title:
            score += 10
        
        # Artist match
        if spotify_artist in yt_title:
            score += 5
        
        # Prefer official content
        if yt_entry.get('channel', '').lower().endswith('- topic'):
            score += 3
        if 'official' in yt_title:
            score += 2
        
        # Penalize likely wrong matches
        if 'cover' in yt_title.lower():
            score -= 5
        if 'remix' in yt_title.lower() and 'remix' not in spotify_title:
            score -= 3
        
        return score

    @staticmethod
    def parse_duration(duration: int):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        duration = []
        if days > 0:
            duration.append('{} days'.format(days))
        if hours > 0:
            duration.append('{} hours'.format(hours))
        if minutes > 0:
            duration.append('{} minutes'.format(minutes))
        if seconds > 0:
            duration.append('{} seconds'.format(seconds))

        return ', '.join(duration)