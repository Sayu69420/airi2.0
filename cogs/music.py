import discord, spotipy, sqlite3, asyncio, time, yt_dlp, shutil, re, requests, json
from discord.ext import commands
from spotipy.oauth2 import SpotifyClientCredentials
from datetime import datetime

import env_config

yt_dlp.utils.bug_reports_message = lambda *args, **kwargs: ''
ydl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'nocheckcertificate': True,
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'no_warnings': True,
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

def get_ffmpeg_path():
    executable = shutil.which('ffmpeg')
    if not executable:
        try:
            import imageio_ffmpeg
            executable = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            executable = 'ffmpeg'
    return executable

FFMPEG_EXECUTABLE = get_ffmpeg_path()

ytdl = yt_dlp.YoutubeDL(ydl_opts)

BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

SPOTIFY_HOSTS = ('spotify.com', 'spotify.link', 'spotify.app.link', 'spoti.fi', 'spotify:')

# How many YouTube results to score before picking one.
SEARCH_CANDIDATES = 6

# Words that mark an upload as something other than the track that was asked
# for. Only counted when the word isn't in the search query itself, so asking
# for a live version still finds one.
JUNK_TERMS = (
    'live', 'cover', 'remix', 'nightcore', 'sped up', 'speed up', 'slowed', 'reverb',
    'karaoke', 'instrumental', 'acoustic', '8d', 'mashup', 'reaction', 'tutorial',
    'full album', 'compilation', 'megamix', 'best of', 'mix', 'loop', 'teaser',
    'trailer', 'preview', 'ringtone', 'ai cover', 'concert', 'edit',
)

# Dropped before comparing a query against a video title — they carry no
# identifying information and would inflate every candidate's overlap score.
QUERY_STOPWORDS = {'the', 'a', 'an', 'of', 'and', 'feat', 'ft', 'official',
                   'audio', 'video', 'lyrics', 'lyric', 'hd', 'hq', 'music'}


def _tokens(text):
    # \w keeps CJK/Cyrillic/accented titles, which an ASCII-only pattern would
    # discard entirely — leaving every candidate with the same overlap score.
    return {t for t in re.findall(r"[\w']+", str(text).lower(), re.UNICODE)
            if t not in QUERY_STOPWORDS}


class Test(commands.Cog):
    def __init__(self, client):
        self.client = client
        # Dictionary storing guild-specific music player states:
        # { guild_id: { 'container': {...}, 'nowplaying': {...}, 'songlist': {...}, 'sent_id': {...} } }
        self.guilds = {}
        # Search query -> the track's real length in seconds, taken from Spotify
        # metadata and used to score YouTube results.
        self._duration_hints = {}

    def _get_guild_id(self, target):
        if hasattr(target, 'guild') and target.guild:
            return target.guild.id
        if hasattr(target, 'message') and target.message and target.message.guild:
            return target.message.guild.id
        return None

    def get_state(self, target):
        gid = self._get_guild_id(target)
        if gid is None:
            return None
        if gid not in self.guilds:
            self.guilds[gid] = {
                'container': {'count': 0, 'is_playing': False, 'queue': []},
                'nowplaying': {'name': ' ', 'url': ' ', 'duration': ' '},
                'songlist': {'loop': False, 'shuffle': False, 's_list': [], 'd_list': [], 'u_list': []},
                'sent_id': {'queue_id': None, 'start_time': None}
            }
        return self.guilds[gid]

    def reset_state(self, target):
        gid = self._get_guild_id(target)
        if gid in self.guilds:
            del self.guilds[gid]

    # ─── Spotify link -> search queries ──────────────────────────────────────
    @staticmethod
    def _artist_name(artists):
        """Flatten Spotify's several artist shapes into a plain string."""
        if isinstance(artists, str):
            names = artists
        elif isinstance(artists, list):
            names = ' '.join(a.get('name', '') if isinstance(a, dict) else str(a) for a in artists)
        else:
            return ''
        return names.replace('\xa0', ' ').replace(',', ' ').strip()

    def _remember_duration(self, query, seconds):
        """Cache a track's real length so search_yt can score candidates by it."""
        if len(self._duration_hints) > 2000:
            self._duration_hints.clear()
        self._duration_hints[query] = int(seconds)

    def _track_query(self, title, artists, duration_ms=None):
        """Build a YouTube search string and note the track's true length."""
        title = str(title or '').replace('\xa0', ' ').strip()
        if not title:
            return None
        query = f"{self._artist_name(artists)} {title}".strip()
        if duration_ms:
            self._remember_duration(query, duration_ms / 1000)
        return query

    def extract_spotify_queries(self, url):
        clean_url = self.clean_spotify_url(url)
        if not clean_url:
            return []
        return (self._spotify_api_queries(clean_url)
                or self._spotify_embed_queries(clean_url)
                or self._spotify_oembed_queries(clean_url))

    @staticmethod
    def _paginate(spotify, page):
        """Collect the items from every page of a Spotify paged response."""
        items = []
        while page:
            items.extend(page.get('items') or [])
            page = spotify.next(page) if page.get('next') else None
        return items

    def _spotify_api_queries(self, url):
        client_id = env_config.get('SPOTIFY_CLIENT_ID')
        client_secret = env_config.get('SPOTIFY_CLIENT_SECRET')
        if not (client_id and client_secret):
            return []
        try:
            spotify = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials(
                client_id=client_id, client_secret=client_secret))
            parts = url.split('/')
            if 'track' in parts:
                track = spotify.track(url, market='IN')
                query = self._track_query(track.get('name'), track.get('artists'), track.get('duration_ms'))
                return [query] if query else []
            if 'playlist' in parts:
                rows = self._paginate(spotify, spotify.playlist_tracks(url, market='IN'))
                tracks = [row.get('track') for row in rows]
            elif 'album' in parts:
                tracks = self._paginate(spotify, spotify.album_tracks(url, market='IN'))
            else:
                return []
            queries = [self._track_query(t.get('name'), t.get('artists'), t.get('duration_ms'))
                       for t in tracks if t]
            return [q for q in queries if q]
        except Exception as e:
            print(f"Spotipy API error: {e}, falling back to web extraction...")
            return []

    def _spotify_embed_queries(self, url):
        """Scrape the public embed page — works without API credentials."""
        embed_url = url if '/embed/' in url else url.replace('open.spotify.com/', 'open.spotify.com/embed/')
        try:
            resp = requests.get(embed_url, headers={'User-Agent': BROWSER_UA}, timeout=10)
        except Exception as e:
            print(f"Embed extraction error: {e}")
            return []
        if resp.status_code != 200:
            return []

        for blob in re.findall(r'<script[^>]*>({[\s\S]*?})</script>', resp.text):
            if not any(k in blob for k in ('props', 'entity', '__NEXT_DATA__')):
                continue
            try:
                data = json.loads(blob)
            except ValueError:
                continue
            page_props = data.get('props', {}).get('pageProps', {})
            entity = page_props.get('state', {}).get('data', {}).get('entity', {}) or page_props.get('entity', {})
            kind = entity.get('type')

            if kind == 'track':
                query = self._track_query(entity.get('title') or entity.get('name'),
                                          entity.get('artists') or entity.get('subtitle'),
                                          entity.get('duration'))
                if query:
                    return [query]
            elif kind in ('playlist', 'album'):
                tracks = entity.get('tracks')
                track_list = (entity.get('trackList')
                              or (tracks.get('items') if isinstance(tracks, dict) else tracks)
                              or [])
                queries = [self._track_query(t.get('title') or t.get('name'),
                                             t.get('artists') or t.get('subtitle') or t.get('artist'),
                                             t.get('duration'))
                           for t in track_list if isinstance(t, dict)]
                queries = [q for q in queries if q]
                if queries:
                    return queries
        return []

    def _spotify_oembed_queries(self, url):
        """Last resort: oEmbed gives the title only, no artist and no duration."""
        try:
            resp = requests.get("https://open.spotify.com/oembed", params={'url': url},
                                headers={'User-Agent': BROWSER_UA}, timeout=5)
            if resp.status_code == 200:
                title = resp.json().get('title')
                if title:
                    return [title.replace('\xa0', ' ').strip()]
        except Exception as e:
            print(f"oEmbed extraction error: {e}")
        return []

    # ─── YouTube search ──────────────────────────────────────────────────────
    def _search_candidates(self, query, limit=SEARCH_CANDIDATES):
        """One flat search request returning `limit` lightweight results."""
        opts = dict(ydl_opts, extract_flat='in_playlist', quiet=True, skip_download=True)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        return [e for e in (info or {}).get('entries') or [] if e]

    def _score_candidate(self, entry, query, target_duration=None):
        """Rank one search result against the track that was asked for.

        Previously the first ytsearch1 hit was taken as-is, which is how covers,
        sped-up edits and hour-long loops kept winning over the real song.
        """
        title = str(entry.get('title') or '')
        lowered = title.lower()
        channel = str(entry.get('channel') or entry.get('uploader') or '').lower()
        query_lower = query.lower()

        query_tokens = _tokens(query)
        # Match against title *and* channel: official uploads often drop the
        # artist from the title because the channel already carries it.
        found = _tokens(f"{title} {channel}")
        overlap = len(query_tokens & found) / len(query_tokens) if query_tokens else 0.0
        score = 2.0 * overlap

        # "<Artist> - Topic" is YouTube's auto-generated upload of the real release.
        if channel.endswith('- topic'):
            score += 0.8
        # A channel named after the artist is usually the artist's own channel.
        elif query_tokens & _tokens(channel):
            score += 0.3
        if 'official' in lowered:
            score += 0.25

        for term in JUNK_TERMS:
            if term not in query_lower and re.search(rf'\b{re.escape(term)}\b', lowered):
                score -= 0.6

        duration = entry.get('duration') or 0
        if target_duration and duration:
            gap = abs(duration - target_duration)
            if gap <= 5:
                score += 0.8
            elif gap <= 15:
                score += 0.3
            else:
                score -= min(2.5, gap / 20.0)
        elif duration:
            if duration < 45:        # shorts and clips
                score -= 0.6
            elif duration > 900:     # 15min+ with no target length to compare against
                score -= 0.4
        return score

    def _best_match_url(self, query, target_duration=None):
        """Return the best-scoring result's watch URL, or None if search failed."""
        try:
            candidates = self._search_candidates(query)
        except Exception as e:
            print(f"YouTube search error for '{query}': {e}")
            return None
        if not candidates:
            return None
        best = max(candidates, key=lambda entry: self._score_candidate(entry, query, target_duration))
        video_id = best.get('id')
        return best.get('url') or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None)

    def search_yt(self, item):
        item_str = str(item).strip()
        # Intercept Spotify URLs: yt-dlp cannot fetch Spotify audio (DRM).
        if any(host in item_str for host in SPOTIFY_HOSTS):
            queries = self.extract_spotify_queries(item_str)
            if not queries:
                return False
            item_str = queries[0]

        if item_str.startswith(('http://', 'https://', 'www.')):
            target = item_str
        else:
            query = item_str.replace('\xa0', ' ').strip()
            # ytsearch1 as the last resort keeps behaviour if scoring finds nothing.
            target = self._best_match_url(query, self._duration_hints.get(item_str)) or f"ytsearch1:{query}"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(target, download=False)
            except Exception as e:
                print(f"search_yt error for '{item}': {e}")
                return False

        if isinstance(info, dict) and info.get('entries'):
            info = info['entries'][0]
        if not isinstance(info, dict):
            return False
        return {
            'url': info.get('url'),
            'title': info.get('title', 'Unknown Title'),
            'duration': info.get('duration', 0),
            'web_url': info.get('webpage_url', item_str),
        }

    def clean_spotify_url(self, raw_url):
        if not raw_url:
            return None
        url = raw_url.strip('<> \t\n\r\'"')
        
        if url.startswith('spotify:'):
            parts = url.split(':')
            if len(parts) >= 3:
                return f"https://open.spotify.com/{parts[1]}/{parts[2]}"
                
        if any(domain in url for domain in ('spotify.link', 'spoti.fi', 'spotify.app.link')):
            try:
                r = requests.get(url, headers={'User-Agent': BROWSER_UA}, allow_redirects=True, timeout=10)
                url = r.url
            except Exception as e:
                print(f"Shortlink expansion error for '{url}': {e}")

        url = url.split('?')[0]
        url = re.sub(r'open\.spotify\.com/intl-[a-z]{2,3}/', 'open.spotify.com/', url)
        return url

    def spotify_ply(self, url, state):
        queries = self.extract_spotify_queries(url)
        if not queries:
            print(f"Could not extract tracks from Spotify URL: {url}")
            return
            
        for q in queries:
            state['container']['queue'].append(q)
            state['songlist']['s_list'].append(q[:50] + '...' if len(q) > 50 else q)
            state['songlist']['d_list'].append('N/A')
            state['songlist']['u_list'].append(q)
        print(f"Spotify queued {len(queries)} track queries")

    def ytube_ply(self, url, state):
        ydl_opts_pl = {
            'extract_flat': True,
            'skip_download': True,
            'quiet': True,
            'noplaylist': False,
        }
        with yt_dlp.YoutubeDL(ydl_opts_pl) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                if info and 'entries' in info and info['entries']:
                    for entry in info['entries']:
                        if entry:
                            v_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
                            if not v_url.startswith('http'):
                                v_url = f"https://www.youtube.com/watch?v={v_url}"
                            v_title = entry.get('title', 'YouTube Track')
                            duration_sec = entry.get('duration') or 0
                            duration_str = time.strftime('%M:%S', time.gmtime(duration_sec)) if duration_sec else 'N/A'

                            state['container']['queue'].append(v_url)
                            state['songlist']['s_list'].append(v_title)
                            state['songlist']['d_list'].append(duration_str)
                            state['songlist']['u_list'].append(v_url)
            except Exception as e:
                print(f"ytube_ply error: {e}")

    @commands.command(aliases=['p', 'play'])
    async def play_function(self, ctx, *, arg=None):
        await self.test_play(ctx, arg)

    async def test_play(self, ctx, arg=None):
        state = self.get_state(ctx)
        voice = discord.utils.get(self.client.voice_clients, guild=ctx.guild)
        try:
            channel = ctx.author.voice.channel
        except AttributeError:
            channel = None
        if arg == None:
            try:
                voice.resume()
                await ctx.message.add_reaction("▶")
            except (AttributeError, discord.ClientException) as e:
                print(f"test_play resume error: {type(e).__name__}: {e}")
                if voice == None or not voice.is_connected():
                    await ctx.send(embed=discord.Embed(description="No songs in the queue !", color=discord.Color.red()))
                else:
                    await ctx.send(embed=discord.Embed(description="Song is already playing !", color=discord.Color.red()))
        else:
            if channel != None:
                if voice == None or not voice.is_connected():
                    if voice and not voice.is_connected():
                        try:
                            await voice.disconnect(force=True)
                        except Exception:
                            pass
                    try:
                        voice = await channel.connect(reconnect=True, timeout=20.0)
                        print('connected')
                    except Exception as e:
                        print(f"Failed to connect to voice channel: {e}")
                        await ctx.send(embed=discord.Embed(description="Failed to connect to the voice channel!", color=discord.Color.red()))
                        return

                initial_queue_len = len(state['container']['queue'])

                clean_arg = arg.strip('<> \t\n\r\'"')
                if any(domain in clean_arg for domain in ['spotify.com', 'spotify.link', 'spotify.app.link', 'spoti.fi', 'spotify:']):
                    await asyncio.to_thread(self.spotify_ply, clean_arg, state)
                    added_count = len(state['container']['queue']) - initial_queue_len
                    if added_count > 0:
                        await ctx.send(embed=discord.Embed(description=f"**Queued** {added_count} tracks", color=discord.Color.green()))
                    else:
                        await ctx.send(embed=discord.Embed(description="Could not queue tracks from Spotify link!", color=discord.Color.red()))
                        return
                elif 'list=' in arg and ('youtube.com' in arg or 'youtu.be' in arg):
                    msg = await ctx.send(embed=discord.Embed(description="Searching on YouTube, It may take a while.", color=discord.Color.red()))
                    await asyncio.to_thread(self.ytube_ply, arg, state)
                    added_count = len(state['container']['queue']) - initial_queue_len
                    if added_count > 0:
                        await msg.edit(embed=discord.Embed(description=f"**Queued** {added_count} tracks", color=discord.Color.red()))
                    else:
                        await msg.edit(embed=discord.Embed(description="Could not extract playlist tracks from YouTube!", color=discord.Color.red()))
                        return
                else:
                    search_res = await asyncio.to_thread(self.search_yt, arg)
                    if search_res:
                        state['container']['queue'].append(search_res['web_url'])
                        state['songlist']['s_list'].append(search_res['title'])
                        state['songlist']['d_list'].append(
                            time.strftime('%M:%S', time.gmtime(search_res['duration'])))
                        state['songlist']['u_list'].append(search_res['web_url'])
                        await ctx.send(embed=discord.Embed(description=f"**Queued** [{search_res['title']}]({search_res['web_url']})", color=discord.Color.blurple()))
                    else:
                        await ctx.send(embed=discord.Embed(description="Could not find the requested song!", color=discord.Color.red()))
                        return

                if state['container']['is_playing'] == False:
                    await self.test_music(ctx)
                else:
                    print('Added to queue')
            else:
                await ctx.message.add_reaction("⚠")
                await ctx.send(embed=discord.Embed(description="Please join a voice channel to use this command !", color=discord.Color.red()))

    async def test_music(self, ctx):
        state = self.get_state(ctx)
        count = state['container']['count']
        if count < 0 or count >= len(state['container']['queue']):
            state['container']['is_playing'] = False
            return

        state['container']['is_playing'] = True
        if state['songlist']['loop'] != 'one':
            state['container']['count'] += 1

        server = ctx.message.guild
        vc = server.voice_client
        if not vc or not vc.is_connected():
            try:
                if ctx.author and ctx.author.voice and ctx.author.voice.channel:
                    vc = await ctx.author.voice.channel.connect(reconnect=True, timeout=20.0)
                else:
                    state['container']['is_playing'] = False
                    return
            except Exception as e:
                print(f"Voice client reconnect failed in test_music: {e}")
                state['container']['is_playing'] = False
                return

        track_item = state['container']['queue'][count]
        if isinstance(track_item, dict):
            target = track_item.get('web_url') or track_item.get('url') or track_item.get('title')
        else:
            target = track_item

        s_dict = await asyncio.to_thread(self.search_yt, target)
        if not s_dict:
            s_dict = {'url': target, 'target': target, 'title': 'Unknown Track', 'duration': 0, 'web_url': target}

        url = s_dict.get('url') or target
        state['nowplaying']['name'] = s_dict.get('title', 'Unknown Track')
        state['nowplaying']['url'] = s_dict.get('web_url', target)
        state['nowplaying']['duration'] = time.strftime(
            '%M:%S', time.gmtime(s_dict.get('duration', 0)))
        state['sent_id']['start_time'] = time.time()

        try:
            vc.play(discord.FFmpegPCMAudio(url, executable=FFMPEG_EXECUTABLE, **ffmpeg_options),
                    after=lambda e: self.test_next(ctx))
            vc.source = discord.PCMVolumeTransformer(vc.source)
        except Exception as e:
            print(f"Error playing audio stream: {e}")
            state['container']['is_playing'] = False
            return

        embed = discord.Embed(
            description=f'**Now Playing** [{state["nowplaying"]["name"]}]({state["nowplaying"]["url"]})', color=discord.Color.blurple())
        await ctx.send(embed=embed)

    def test_next(self, ctx):
        state = self.get_state(ctx)
        try:
            if len(state['container']['queue']) > state['container']['count']:
                state['container']['is_playing'] = True
                count = state['container']['count']
                print(f">>>> {count}")
                if state['songlist']['loop'] != 'one':
                    state['container']['count'] += 1

                server = ctx.message.guild
                vc = server.voice_client
                if not vc or not vc.is_connected():
                    state['container']['is_playing'] = False
                    return

                track_item = state['container']['queue'][count]
                if isinstance(track_item, dict):
                    target = track_item.get('web_url') or track_item.get('url') or track_item.get('title')
                else:
                    target = track_item

                s_dict = self.search_yt(target)
                if not s_dict:
                    s_dict = {'url': target, 'title': 'Unknown Track', 'duration': 0, 'web_url': target}

                url = s_dict.get('url') or target
                state['nowplaying']['name'] = s_dict.get('title', 'Unknown Track')
                state['nowplaying']['url'] = s_dict.get('web_url', target)
                state['nowplaying']['duration'] = time.strftime(
                    '%M:%S', time.gmtime(s_dict.get('duration', 0)))
                state['sent_id']['start_time'] = time.time()

                vc.play(discord.FFmpegPCMAudio(url, executable=FFMPEG_EXECUTABLE, **ffmpeg_options),
                        after=lambda e: self.test_next(ctx))
                vc.source = discord.PCMVolumeTransformer(vc.source)

            else:
                if state['songlist']['loop'] == True and len(state['container']['queue']) > 0:
                    state['container']['count'] = 0
                    self.test_next(ctx)
                else:
                    state['container']['is_playing'] = False
        except Exception as e:
            print(f"test_next error: {e}")
            state['container']['is_playing'] = False

    @commands.command(aliases=['pause', 'stop', 's'])
    async def tpause_(self, ctx):
        voice = discord.utils.get(ctx.bot.voice_clients, guild=ctx.guild)
        if voice and voice.is_playing():
            voice.pause()
            await ctx.message.add_reaction("⏸")

    @commands.command(aliases=['np', 'song'])
    async def now_playing(self, ctx):
        state = self.get_state(ctx)
        y = int(time.time())
        x = int(state['sent_id']['start_time']) if state['sent_id']['start_time'] else y
        tp = time.strftime('%M:%S', time.gmtime(y-x))
        tn = state['nowplaying']['duration']
        m, s = int(tn.split(':')[0]), int(tn.split(':')[1]) if ':' in tn else (0, 0)
        msg = ' '
        for i in range(20):
            total_sec = m * 60 + s
            fraction = (int(y-x)/total_sec)*20 if total_sec > 0 else 0
            if fraction >= i and fraction < i+1:
                msg = f"{msg}🔵"
            else:
                msg = f"{msg}▬"
        song = state['nowplaying']["name"]
        song = f"{song[:44]}.." if len(song) > 43 else song
        embed = discord.Embed(
            description=f"**Now Playing** [{song}]({state['nowplaying']['url']})", color=discord.Color.blurple())
        embed.set_footer(text=f"{msg} {tp}/{state['nowplaying']['duration']}")
        await ctx.send(embed=embed)

    @commands.command(aliases=['loop', 'l'])
    async def tloop_(self, ctx):
        state = self.get_state(ctx)
        if state['songlist']['loop'] == False:
            state['songlist']['loop'] = True
            await ctx.message.add_reaction("🔁")
        elif state['songlist']['loop'] == True:
            state['songlist']['loop'] = 'one'
            state['container']['count'] -= 1
            print(f">>> {state['container']['count']}")
            await ctx.message.add_reaction("🔂")
        else:
            state['songlist']['loop'] = False
            state['container']['count'] += 1
            await ctx.message.add_reaction("↪")

    @commands.command(aliases=['shuffle'])
    async def tsuffle_(self, ctx):
        state = self.get_state(ctx)
        if state['songlist']['shuffle'] == False:
            state['songlist']['shuffle'] = True
            await ctx.message.add_reaction("🔀")
        else:
            state['songlist']['shuffle'] = False
            await ctx.message.add_reaction("👍")

    @commands.command(aliases=['next', 'n', 'skip'])
    async def tnext_(self, ctx):
        state = self.get_state(ctx)
        voice = discord.utils.get(ctx.bot.voice_clients, guild=ctx.guild)
        if len(state['songlist']['s_list']) >= state['container']['count']:
            if voice:
                voice.stop()
            await ctx.message.add_reaction('👌')
            await asyncio.sleep(4)
            embed = discord.Embed(
                description=f'**Now Playing** [{state["nowplaying"]["name"]}]({state["nowplaying"]["url"]})', color=discord.Color.blurple())
            await ctx.send(embed=embed)

    @commands.command(aliases=['prev', 'ps', 'previous'])
    async def tprevious_(self, ctx):
        state = self.get_state(ctx)
        voice = discord.utils.get(ctx.bot.voice_clients, guild=ctx.guild)
        if state['container']['count'] > 1:
            state['container']['count'] -= 2
            if voice:
                voice.stop()
            await ctx.message.add_reaction('👌')
            await asyncio.sleep(4)
            embed = discord.Embed(
                description=f'**Now Playing** [{state["nowplaying"]["name"]}]({state["nowplaying"]["url"]})', color=discord.Color.blurple())
            await ctx.send(embed=embed)

    @commands.command(aliases=['jump', 'j'])
    async def tjump_(self, ctx, num: int = None):
        if num != None:
            state = self.get_state(ctx)
            voice = discord.utils.get(ctx.bot.voice_clients, guild=ctx.guild)
            try:
                state['container']['count'] = num - 1
                if voice:
                    voice.stop()
                await ctx.message.add_reaction('👌')
                await asyncio.sleep(4)
                embed = discord.Embed(
                    description=f'**Now Playing** [{state["nowplaying"]["name"]}]({state["nowplaying"]["url"]})', color=discord.Color.blurple())
                await ctx.send(embed=embed)
            except Exception as e:
                print(f"tjump_ error: {type(e).__name__}: {e}")
                await ctx.send(embed=discord.Embed(description="Jump index not valid !", color=discord.Color.red()))
        else:
            await ctx.send(embed=discord.Embed(description="Jump index not given !", color=discord.Color.red()))

    @commands.command(aliases=['grab'])
    async def tgrab_(self, ctx):
        state = self.get_state(ctx)
        voice = discord.utils.get(ctx.bot.voice_clients, guild=ctx.guild)
        if voice != None:
            embed = discord.Embed(title='Your Requesed Song :',
                                  description=f"[{state['nowplaying']['name']}]({state['nowplaying']['url']})", color=discord.Color.blurple())
            await ctx.author.send(embed=embed)
            msg = await ctx.send(embed=discord.Embed(description="Direct Message sent !!", color=discord.Color.greyple()))
            await asyncio.sleep(5)
            await msg.delete()
        else:
            await ctx.message.add_reaction("⚠")
            await ctx.send(embed=discord.Embed(description="No songs playing !!", color=discord.Color.red()))

    @commands.command(aliases=['q', 'queue'])
    async def tqueue_(self, ctx):
        state = self.get_state(ctx)
        count = state['container']['count'] - 1
        vc_id = ctx.author.voice.channel.id
        embed = await self.queue_create(vc_id, state, count)
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("🔼")
        await msg.add_reaction("🔽")
        state['sent_id']['queue_id'] = msg.id

    async def queue_create(self, vc_id, state, count):
        if state['songlist']['loop'] == 'one':
            count += 1
        if len(state['container']['queue']) > 10:
            num = 10
        else:
            num = len(state['container']['queue'])

        songs, duration = "\n", "\n"
        start = int(count/10)*10
        end = int(count+num)
        print(f"count:{count},num:{num}")
        print(f"start: {start}, end: {end}")
        if len(state['container']['queue']) < end:
            end = len(state['container']['queue'])

        for i in range(start, end):
            if i+1 == state['container']["count"]:
                name = state['songlist']['s_list'][i]
                y = int(time.time())
                x = int(state['sent_id']['start_time']) if state['sent_id']['start_time'] else y
                tp = time.strftime('%M:%S', time.gmtime(y-x))
                tdelta = datetime.strptime(
                    state['nowplaying']['duration'], '%M:%S') - datetime.strptime(tp, '%M:%S')
                name = f'{name[:45]}..' if len(name) > 47 else name
                songs = f"{songs}\n⬐ now playing\n> {i+1}) [**{name}**]({state['nowplaying']['url']})\n⬑ now playing"
                duration = f"{duration}\n\u200b\n{str(tdelta)[2:]} **left**\n \u200b "
            else:
                name = state['songlist']['s_list'][i]
                name = f'{name[:45]}..' if len(name) > 47 else name
                songs = f"{songs}\n {i+1}) {name}"
                duration = f"{duration}\n {state['songlist']['d_list'][i]}"

        embed = discord.Embed(
            title='🎵 | Queued Songs', description=f'Join  <#{vc_id}>', color=discord.Color.blurple())
        embed.add_field(name='__Song Name__',
                        value=f'\n{songs}\n', inline=True)
        embed.add_field(name="__Duration__",
                        value=f'\n{duration}\n', inline=True)
        embed.add_field(
            name="\u200b", value=f'\n**{len(state["songlist"]["s_list"])-end}** more Track(s)', inline=False)
        embed.set_footer(
            text=f"Loop : {'🔁'if state['songlist']['loop']== True else ('🔄' if state['songlist']['loop']=='one' else '❌')}      Shuffle: {'🔀'if state['songlist']['shuffle']== True else '❌'}")
        return embed

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            pass
        else:
            try:
                state = self.get_state(reaction)
                vc_id = user.voice.channel.id
                if reaction.message.id == state['sent_id']['queue_id']:
                    count = state['container']['count'] - 1
                    if reaction.emoji == '🔽':
                        embed = await self.queue_create(vc_id, state, count+10)
                    elif reaction.emoji == '🔼':
                        embed = await self.queue_create(vc_id, state, count)

                    await reaction.message.edit(embed=embed)
            except AttributeError:
                pass  # reacting user isn't in a voice channel
            except Exception as e:
                print(f"on_reaction_add error: {type(e).__name__}: {e}")

    @commands.command(aliases=['leave'])
    async def tleave_(self, ctx):
        server = ctx.message.guild
        voice_channel = server.voice_client
        voice = discord.utils.get(ctx.bot.voice_clients, guild=ctx.guild)
        self.reset_state(ctx)

        if voice:
            voice.stop()
        if voice_channel:
            await voice_channel.disconnect()
        await ctx.message.add_reaction("👋")

    @commands.command(aliases=['clear'])
    async def tclear_(self, ctx):
        state = self.get_state(ctx)
        state['container']['queue'].clear()
        state['container']['count'] = 0
        state['songlist']['s_list'].clear()
        state['songlist']['d_list'].clear()
        state['container']['is_playing'] = False

        voice = discord.utils.get(ctx.bot.voice_clients, guild=ctx.guild)
        if voice:
            voice.stop()
        await ctx.send(embed=discord.Embed(description="Queue is cleared !", color=discord.Color.orange()))

    @commands.command(aliases=['remove', 'pop'])
    async def remove_(self, ctx, num: int = None):
        state = self.get_state(ctx)
        if num != None:
            song = state['songlist']['s_list'][num-1]
            state['container']['queue'].pop(num-1)
            state['songlist']['s_list'].pop(num-1)
            state['songlist']['d_list'].pop(num-1)
            await ctx.send(embed=discord.Embed(description=f"**Removed** {song}"))
        else:
            await ctx.send(embed=discord.Embed(description="Argument missing ! **<number>**", color=discord.Color.red()))

    # LIKED SONGS
    def _init_liked_db(self):
        # Schema lives in env_config.SCHEMA and is applied at startup.
        env_config.init_db()

    @commands.command(aliases=['like'])
    async def tlike_(self, ctx):
        self._init_liked_db()
        x = ctx.message.author.id
        state = self.get_state(ctx)
        if state and state['container']['is_playing'] == True:
            song = state['nowplaying']['name']
            url = state['nowplaying']['url']
            duration = state['nowplaying']['duration']
            with sqlite3.connect(env_config.DB_PATH) as conn:
                c = conn.cursor()
                c.execute("INSERT INTO liked (user_id, song, url, duration) VALUES (?,?,?,?)", (x, song, url, duration))
                conn.commit()
            await ctx.send(embed=discord.Embed(description="Added to Liked Songs.", color=discord.Color.green()))
        else:
            await ctx.send(embed=discord.Embed(description="No song's playing !", color=discord.Color.red()))

    @commands.command(aliases=['get'])
    async def tgetlist(self, ctx):
        self._init_liked_db()
        user_id = ctx.message.author.id
        try:
            with sqlite3.connect(env_config.DB_PATH) as conn:
                c = conn.cursor()
                c.execute("SELECT song, url, duration FROM liked WHERE user_id = ?", (user_id,))
                rows = c.fetchall()

            if not rows:
                return await ctx.send(embed=discord.Embed(description="No Songs Found, Add songs using `.like` to see it.", color=discord.Color.red()))

            songs = ""
            for i, row in enumerate(rows):
                songs += f"\n{i+1}. {row[0]}"
            await ctx.send(embed=discord.Embed(description=f"**Liked Songs | {ctx.author.mention}**\n{songs}", color=discord.Color.orange()))
        except Exception as e:
            print(f"Liked songs fetch error: {type(e).__name__}: {e}")
            await ctx.send(embed=discord.Embed(description="Failed to fetch Liked Songs.", color=discord.Color.red()))

    @commands.command(aliases=['dislike', 'dl'])
    async def tremove_song(self, ctx, num=None):
        self._init_liked_db()
        user_id = ctx.message.author.id
        if num is not None:
            try:
                num = int(num)
                with sqlite3.connect(env_config.DB_PATH) as conn:
                    c = conn.cursor()
                    c.execute("SELECT song FROM liked WHERE user_id = ?", (user_id,))
                    rows = c.fetchall()
                    if num < 1 or num > len(rows):
                        return await ctx.send(embed=discord.Embed(description="Invalid song index!", color=discord.Color.red()))
                    song = rows[num - 1][0]
                    c.execute("DELETE FROM liked WHERE user_id = ? AND song = ?", (user_id, song))
                    conn.commit()
                await ctx.send(embed=discord.Embed(description="Removed from Liked Songs !", color=discord.Color.from_rgb(157, 207, 209)))
            except ValueError:
                await ctx.send(embed=discord.Embed(description="Please provide a valid number!", color=discord.Color.red()))
        else:
            await ctx.send(embed=discord.Embed(description="Please specify a song number to remove!", color=discord.Color.red()))

    @commands.command(aliases=['p_liked', 'play_liked', 'pliked', 'plike'])
    async def play_liked_(self, ctx, num=None):
        self._init_liked_db()
        user_id = ctx.message.author.id
        with sqlite3.connect(env_config.DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT song, url, duration FROM liked WHERE user_id = ?", (user_id,))
            rows = c.fetchall()

        if not rows:
            return await ctx.send(embed=discord.Embed(description="No Liked Songs found!", color=discord.Color.red()))

        if num is not None:
            try:
                num = int(num)
                if num < 1 or num > len(rows):
                    return await ctx.send(embed=discord.Embed(description="Invalid song index!", color=discord.Color.red()))
                url = rows[num - 1][1]
                await self.test_play(ctx, url)
            except ValueError:
                await ctx.send(embed=discord.Embed(description="Please provide a valid number!", color=discord.Color.red()))
        else:
            voice = discord.utils.get(ctx.bot.voice_clients, guild=ctx.guild)
            if voice is None:
                await ctx.send(embed=discord.Embed(description=f"**Queued** {len(rows)} songs.", color=discord.Color.orange()))
                await self.test_play(ctx, rows[0][1])
                state = self.get_state(ctx)
                if state is not None:
                    for song, url, duration in rows[1:]:
                        state['container']['queue'].append(url)
                        state['songlist']['s_list'].append(song)
                        state['songlist']['d_list'].append(duration)
            else:
                try:
                    state = self.get_state(ctx)
                    if state is not None and state['container']['is_playing'] == True:
                        for song, url, duration in rows:
                            state['container']['queue'].append(url)
                            state['songlist']['s_list'].append(song)
                            state['songlist']['d_list'].append(duration)
                        await ctx.send(embed=discord.Embed(description=f"**Queued** {len(rows)} songs.", color=discord.Color.orange()))
                    else:
                        await ctx.send(embed=discord.Embed(description=f"**Queued** {len(rows)} songs.", color=discord.Color.orange()))
                        await self.test_play(ctx, rows[0][1])
                        state = self.get_state(ctx)
                        if state is not None:
                            for song, url, duration in rows[1:]:
                                state['container']['queue'].append(url)
                                state['songlist']['s_list'].append(song)
                                state['songlist']['d_list'].append(duration)
                except Exception as e:
                    print(f'cant play: {e}')

    @commands.command(aliases=['spotifyp', 'spp'])
    async def spotify_playlist_search(self, ctx, *, arg):
        client_id = env_config.get('SPOTIFY_CLIENT_ID')
        client_secret = env_config.get('SPOTIFY_CLIENT_SECRET')
        if not (client_id and client_secret):
            return await ctx.send(embed=discord.Embed(
                description="Spotify search needs `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` in `.env`.",
                color=discord.Color.red()))
        spotify = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials(
            client_id=client_id, client_secret=client_secret))
        msg = await ctx.send(embed=discord.Embed(description=f"**Searching** - {arg} on Spotify", color=discord.Color.gold()))
        var = arg
        arg = arg.split(" ")
        q = " "
        for i in range(len(arg)):
            q = f"{q}{arg[i]}%"
        q = q[1:-1]
        try:
            search = await asyncio.to_thread(
                spotify.search, q, limit=1, offset=0, type='playlist', market='IN')
            url = search['playlists']['items'][0]['external_urls']['spotify']
            await ctx.send(url)
            await msg.delete()
            await self.test_play(ctx, url)
        except Exception as e:
            print(f"spotify_playlist_search error for '{var}': {type(e).__name__}: {e}")
            await msg.edit(embed=discord.Embed(description=f"Failed to get playlist named `{var}`", color=discord.Color.orange()))

async def setup(client):
    await client.add_cog(Test(client))
