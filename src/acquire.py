# -*- coding: utf-8 -*-
"""
Audio acquisition for the DeckCastDJ CD burner.

This module is deliberately free of any Flask dependency so it can be used
from the web app, from cdburn's CLI, or on its own.

It provides a small provider abstraction ("TrackSource") so the two places
music can come from - YouTube and the external music host used by the /mp3
page - produce the exact same track records.  Everything downstream (plan,
premaster, burn) is therefore source agnostic.

Nothing here touches trackext.py; the YouTube plumbing is intentionally a
separate copy so the Track Numbers feature can never be broken by changes
made for CD burning.

@author: Nathan
"""

import os
import re
import json
import time
import shutil
import urllib.parse
import urllib.request
import urllib.error

try:
    import yt_dlp
except Exception:  # pragma: no cover - only hit when the dep is missing
    yt_dlp = None

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

# Root of the on-disk media cache.  Everything bulky (downloaded sources and
# premastered WAVs) lives under here so it stays out of data/.
MEDIA_DIR = 'ytd'
MUSIC_DIR = os.path.join(MEDIA_DIR, 'music')
CD_DIR = os.path.join(MEDIA_DIR, 'cd')

# Where tracks pulled off the music host are staged for the burner.  Kept out
# of MUSIC_DIR on purpose: that tree holds the cached audiodb index and can be
# pointed at a real library, so nothing in it is ever deleted automatically,
# while these download copies are dropped as soon as the CD-ready WAV exists.
MUSIC_SRC_DIR = os.path.join(MEDIA_DIR, 'music_src')

# Finished MP3 downloads, kept (not a cache): the DJ page's Download button puts
# them here.  Deliberately outside the trees a cache clean sweeps, so clearing
# burner media or sources can never delete a downloaded track.
MP3_DIR = os.path.join(MEDIA_DIR, 'mp3')

AUDIODB_NAME = 'audiodb.txt'
AUDIODB_MAX_AGE_SECONDS = 24 * 60 * 60

# Base URL of the music host that serves the /mp3 library.  main.py hands this
# same value to templates/mp3.html, so this is the one place to change when the
# host moves - and it can be overridden without touching code via
# "musicBaseUrl" in data/settings.json (a LAN address and the public name can
# differ depending on where the browser is).
DEFAULT_MUSIC_BASE_URL = 'http://pi86.sytes.net/music/'

SETTINGS_FILE = 'data/settings.json'

_MUSIC_BASE_URL_CACHE = None

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _app_dir():
    """Project root - the folder holding main.py, data/, templates/ and ytd/.

    When frozen the app runs from the executable's folder.  Otherwise this
    file lives in src/, so the project root is its parent directory.
    """
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_settings():
    """Read data/settings.json as a dict, tolerating any failure."""
    try:
        path = os.path.join(_app_dir(), SETTINGS_FILE)
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as fp:
                data = json.load(fp)
                if isinstance(data, dict):
                    return data
    except Exception as e:
        print('acquire: could not read settings.json:', e)
    return {}


def get_music_base_url():
    """Base URL of the music host, overridable via settings.json."""
    global _MUSIC_BASE_URL_CACHE
    if _MUSIC_BASE_URL_CACHE is None:
        url = str(load_settings().get('musicBaseUrl', '') or '').strip()
        if not url:
            url = DEFAULT_MUSIC_BASE_URL
        if not url.endswith('/'):
            url += '/'
        _MUSIC_BASE_URL_CACHE = url
    return _MUSIC_BASE_URL_CACHE


def sanitize_filename(name, fallback='track'):
    """Make a string safe to use as a file name on Windows and macOS."""
    name = str(name or '')
    # strip characters that are illegal on one platform or the other
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip(' .')
    if not name:
        name = fallback
    return name[:120]


def human_bytes(size):
    """Format a byte count for display."""
    value = float(size or 0)
    for unit in ('B', 'KB', 'MB'):
        if value < 1024:
            if unit == 'B':
                return '{0:.0f} B'.format(value)
            return '{0:.1f} {1}'.format(value, unit)
        value /= 1024
    return '{0:.2f} GB'.format(value)


def find_ffmpeg():
    """Locate ffmpeg.  Prefers a copy bundled next to the app."""
    exe = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
    local = os.path.join(_app_dir(), exe)
    if os.path.isfile(local):
        return local
    found = shutil.which('ffmpeg')
    if found:
        return found
    # last resort: assume it is on PATH under the plain name
    return exe


def expected_cookies_path():
    """Where a cookies.txt export belongs: next to the app."""
    return os.path.join(_app_dir(), 'cookies.txt')


def find_cookies():
    """Find a YouTube cookies.txt, if the user supplied one.

    This file is the only credential source: the app never reads the cookies out
    of an installed browser.
    """
    app = _app_dir()
    for path in (
        expected_cookies_path(),
        os.path.join(os.getcwd(), 'cookies.txt'),
        os.path.join(app, MEDIA_DIR, 'cookies.txt'),
    ):
        if os.path.isfile(path):
            _warn_if_anonymous(path)
            return path
    _warn_if_missing(app)
    return None


# yt-dlp overwrites the cookie file it is handed with its own jar, and that jar
# only holds what that run saw - which strips the authentication cookies out of
# a browser export.  Keep a private copy for yt-dlp to scribble on instead, so
# the user's cookies.txt stays exactly as exported.
_COOKIE_COPY = {'key': None, 'path': None}
_ANON_WARNED = set()
_MISSING_WARNED = set()


def _cookie_names(path):
    """Names of the cookies held in a Netscape-format jar."""
    names = set()
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fp:
            for line in fp:
                if not line.strip() or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) >= 7:
                    names.add(parts[5].strip())
    except OSError:
        pass
    return names


def _looks_anonymous(path):
    """True when a cookie jar has no signed-in session in it.

    Matched on exact cookie names: the anonymous jars YouTube hands out also
    carry names like __Secure-3PSID and __Secure-1PSIDTS, so a substring test
    would call those signed in.
    """
    signed_in = {'SID', 'HSID', 'SSID', 'LOGIN_INFO'}
    names = _cookie_names(path)
    if not names:
        return False          # unreadable or empty: not worth guessing
    return not (names & signed_in)


def _warn_if_anonymous(path):
    if path in _ANON_WARNED:
        return
    _ANON_WARNED.add(path)
    if _looks_anonymous(path):
        print('acquire: {0} has no sign-in cookies (SID/HSID/SSID/LOGIN_INFO), so '
              'YouTube treats it as anonymous.  Re-export it with the '
              "'Get cookies.txt LOCALLY' extension while signed in to YouTube."
              .format(path), flush=True)


def _warn_if_missing(app):
    if app in _MISSING_WARNED:
        return
    _MISSING_WARNED.add(app)
    print('acquire: no cookies.txt found in {0} (or its ytd/ folder), so requests '
          'to YouTube are anonymous and may be refused with "Sign in to confirm '
          "you're not a bot\".  Export one from a signed-in YouTube session with "
          "the 'Get cookies.txt LOCALLY' extension and save it as cookies.txt in "
          'that folder.'.format(app), flush=True)


def private_cookie_file(path):
    """A throwaway copy of a cookie file, refreshed whenever it drifts.

    The copy is made because yt-dlp saves its cookie jar back over whatever file
    it was handed, which would strip the sign-in cookies out of the user's
    export.  It has to be re-made more often than "when the export changes",
    though: a run that ends early - a bot check, a geo error - writes back only
    the handful of cookies it happened to see, which quietly turns the copy
    anonymous and makes every later attempt fail while cookies.txt is perfectly
    good.
    """
    try:
        stat = os.stat(path)
        key = (os.path.abspath(path), stat.st_mtime, stat.st_size)
        dest = _COOKIE_COPY['path']
        if (_COOKIE_COPY['key'] == key and dest and os.path.isfile(dest)
                and _matches_cookie_source(path, dest)):
            return dest
        dest_dir = os.path.join(_app_dir(), MEDIA_DIR)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, '.youtube-cookies.txt')
        shutil.copyfile(path, dest)
        _COOKIE_COPY.update(key=key, path=dest)
        return dest
    except Exception as e:
        print('acquire: could not copy the cookie file:', e)
        return path


def _matches_cookie_source(source, copy):
    """True when the copy still holds every cookie the export does."""
    try:
        if os.path.getsize(copy) != os.path.getsize(source):
            return False
        names = _cookie_names(source)
        return bool(names) and names.issubset(_cookie_names(copy))
    except OSError:
        return False


# yt-dlp's --cookies-from-browser is deliberately not used: it would read a
# signed-in session straight out of the user's browser profile.  cookies.txt is
# the only credential source.


def find_js_runtime():
    """Find Deno or Node so yt-dlp can solve YouTube's JS challenges."""
    deno_candidates = [
        shutil.which('deno'),
        os.path.join(_app_dir(), 'deno.exe'),
        os.path.join(_app_dir(), 'deno'),
        os.path.expandvars(r'%USERPROFILE%\.deno\bin\deno.exe'),
        os.path.expandvars(r'%LOCALAPPDATA%\Programs\deno\deno.exe'),
        os.path.expanduser('~/.deno/bin/deno'),
        '/opt/homebrew/bin/deno',
        '/usr/local/bin/deno',
        '/usr/bin/deno',
    ]
    deno = next((p for p in deno_candidates if p and os.path.isfile(p)), None)
    if deno:
        return ('deno', deno)

    node_candidates = [
        shutil.which('node'),
        '/usr/bin/node',
        '/usr/local/bin/node',
        '/opt/homebrew/bin/node',
    ]
    node = next((p for p in node_candidates if p and os.path.isfile(p)), None)
    if node:
        return ('node', node)
    return (None, None)


class _YDLLogger(object):
    """Captures yt-dlp's error output so real failures can be reported.

    yt-dlp sends errors to logger.error() and sometimes prefixes warnings
    with "ERROR:", so both are collected.
    """

    def __init__(self):
        self.errors = []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        if str(msg).startswith('ERROR:'):
            self.errors.append(str(msg))

    def error(self, msg):
        self.errors.append(str(msg))

    def last_error(self):
        return self.errors[-1] if self.errors else ''


def ytdlp_shared_opts():
    """The yt-dlp options every YouTube caller in the app should use.

    Cookies (handed over as a private copy, see private_cookie_file) plus a JS
    runtime for YouTube's signature challenges.  It lives here so the burner,
    Track Numbers extraction and the pafy lookups behind the DJ page cannot
    drift apart - that drift is what left the DJ page being told to sign in
    while the rest of the app was authenticated.
    """
    opts = {}
    cookies = find_cookies()
    if cookies:
        opts['cookiefile'] = private_cookie_file(cookies)
    kind, path = find_js_runtime()
    if kind:
        opts['js_runtimes'] = {kind: {'path': path}}
    return opts


def _ytdlp_base_opts(logger=None):
    """Options shared by info extraction and downloading."""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': False,
        'remote_components': ['ejs:github', 'ejs:npm'],
        'extractor_args': {
            'youtube': {
                'player_client': ['web', 'android', 'ios'],
            }
        },
    }
    if logger is not None:
        opts['logger'] = logger
    opts.update(ytdlp_shared_opts())
    return opts


def _bot_check_message(error):
    """Friendly guidance when YouTube blocks automated downloads."""
    text = str(error)
    if 'Sign in to confirm' in text or 'bot' in text.lower() or '429' in text:
        return (
            'YouTube is blocking automated requests from this machine '
            '({0}).  The usual cause is that cookies.txt is missing, has no '
            'signed-in session in it, or has gone stale.  Export it again with '
            "the 'Get cookies.txt LOCALLY' extension while signed in to YouTube "
            '- it must contain SID, HSID, SSID and LOGIN_INFO - and save it as '
            'cookies.txt next to the app.  If the error mentions a signature or '
            'JS challenge, also install Deno or Node.js.'
            .format(text)
        )
    return 'Error fetching from YouTube: {0}'.format(text)


def http_get(url, timeout=60):
    """Simple GET returning a bytes response."""
    req = urllib.request.Request(url, headers={'User-Agent': 'DeckCastDJ'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---------------------------------------------------------------------------
# Track sources
# ---------------------------------------------------------------------------


class TrackSource(object):
    """Base class: resolves a reference to a track record and fetches audio."""

    name = 'base'

    def resolve(self, ref, log=None):
        """Return a list of track dicts describing ref.  Raises on failure.

        log is an optional callable used to report progress to the caller.
        """
        raise NotImplementedError

    def acquire(self, track, dest_dir, log=None, progress=None):
        """Ensure the audio for track is on disk; return the local path.

        log is an optional callable that receives status lines, and progress
        an optional callable receiving (percent, text) for live updates within
        a single download.
        """
        raise NotImplementedError

    # -- shared record helper -------------------------------------------------
    def make_track(self, ref, title, artist, seconds, bitrate=0):
        return {
            'source': self.name,
            'ref': ref,
            'title': title or 'Unknown',
            'artist': artist or '',
            'seconds': int(seconds or 0),
            'bitrate': int(bitrate or 0),
            'srcFile': None,
        }


class YouTubeSource(TrackSource):
    """Resolves and downloads audio from YouTube via yt-dlp."""

    name = 'youtube'

    def resolve(self, ref, log=None):
        if yt_dlp is None:
            raise RuntimeError('yt-dlp is not installed; cannot fetch from YouTube.')

        url = ref
        if not ref.lower().startswith('http'):
            url = 'https://www.youtube.com/watch?v=' + ref

        ydlLogger = _YDLLogger()
        opts = _ytdlp_base_opts(logger=ydlLogger)
        opts['socket_timeout'] = 30
        # Playlists are read "flat": it is dramatically faster than fetching
        # every video's full metadata (seconds instead of minutes), it still
        # reports durations, and it never touches the individual videos - so a
        # single blocked or removed video cannot sink the whole playlist.
        #
        # "ignoreerrors" is deliberately NOT used: it converts a real failure
        # into a silent None, which is far harder to diagnose.
        if 'list=' in url:
            opts['extract_flat'] = 'in_playlist'

        if log:
            log('Reading {0} ...'.format(url))

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            raise RuntimeError(_bot_check_message(e))

        if not info:
            detail = ydlLogger.last_error() or 'YouTube returned no data'
            raise RuntimeError(_bot_check_message(detail))

        if info.get('_type') == 'playlist':
            entries = [e for e in (info.get('entries') or []) if e]
            tracks = []
            skipped = 0
            unknownDurations = 0

            for entry in entries:
                entryId = entry.get('id') or ''
                title = entry.get('title') or ''
                seconds = entry.get('duration') or 0

                # drop entries with no identity and no metadata at all, which
                # is what a removed or private video looks like in flat mode
                if not entryId or (not title and not seconds):
                    skipped += 1
                    continue

                if not seconds:
                    unknownDurations += 1

                tracks.append(self.make_track(
                    entryId,
                    title,
                    entry.get('uploader') or entry.get('channel') or '',
                    seconds,
                ))

            if log:
                log('Playlist "{0}": {1} video(s)'.format(
                    info.get('title') or url, len(tracks)))
                if skipped:
                    log('Skipped {0} unavailable video(s).'.format(skipped))
                if unknownDurations:
                    log('{0} video(s) have no duration; they are checked '
                        'when the audio is downloaded.'.format(unknownDurations))

            if not tracks:
                raise RuntimeError('No usable videos were found in that playlist.')

            return tracks

        return [self.make_track(
            info.get('id', ''),
            info.get('title', 'Unknown'),
            info.get('uploader') or info.get('channel') or '',
            info.get('duration') or 0,
        )]

    def acquire(self, track, dest_dir, log=None, progress=None):
        if yt_dlp is None:
            raise RuntimeError('yt-dlp is not installed; cannot fetch from YouTube.')

        os.makedirs(dest_dir, exist_ok=True)
        url = 'https://www.youtube.com/watch?v=' + track['ref']
        title = track.get('title') or track['ref']

        # NOTE: the "_src" suffix keeps this file distinct from the
        # "<videoId>.<ext>" file trackext.py downloads (and deletes) for its
        # own analysis, so the two caches can never clobber each other.
        ydlLogger = _YDLLogger()
        opts = _ytdlp_base_opts(logger=ydlLogger)
        opts.update({
            'format': 'bestaudio/best',
            'socket_timeout': 30,
            'outtmpl': os.path.join(dest_dir, '%(id)s_src.%(ext)s'),
        })

        # yt-dlp fires these hooks for every downloaded block, so throttle to
        # whole-percent changes (and at most twice a second) before reporting.
        if progress:
            state = {'percent': -1, 'at': 0.0}

            def report(status):
                if status.get('status') == 'finished':
                    progress(100, 'Downloaded {0} ({1})'.format(
                        title, human_bytes(status.get('total_bytes') or 0)))
                    return
                if status.get('status') != 'downloading':
                    return
                total = status.get('total_bytes') or status.get('total_bytes_estimate')
                done = status.get('downloaded_bytes') or 0
                if not total:
                    return
                percent = int(done * 100 / total)
                now = time.time()
                if percent <= state['percent'] or now - state['at'] < 0.5:
                    return
                state['percent'] = percent
                state['at'] = now
                progress(percent, 'Downloading {0}: {1}% of {2}'.format(
                    title, percent, human_bytes(total)))

            opts['progress_hooks'] = [report]

        if log:
            log('Downloading: {0}'.format(title))

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as e:
            raise RuntimeError(_bot_check_message(e))

        if not info:
            detail = ydlLogger.last_error() or 'YouTube returned no data'
            raise RuntimeError(_bot_check_message(detail))

        # yt-dlp tells us exactly where it put the file
        downloads = info.get('requested_downloads') or []
        path = downloads[0].get('filepath') if downloads else None
        if not path or not os.path.isfile(path):
            guess = os.path.join(dest_dir, '{0}_src.{1}'.format(
                info.get('id', track['ref']), info.get('ext', 'webm')))
            path = guess if os.path.isfile(guess) else None
        if not path:
            raise RuntimeError('Download appeared to succeed but the file was not found.')

        if log:
            log('  downloaded {0} ({1})'.format(
                os.path.basename(path), human_bytes(os.path.getsize(path))))
        return path


def mp3_path(video_id):
    """Path of an already-downloaded MP3 for a video, or None.

    Matched on the video id rather than the whole name: the title in the file
    name changes when YouTube edits it, and a changed title must not make an
    existing download look missing (and get downloaded again).
    """
    if not os.path.isdir(MP3_DIR):
        return None
    video_id = str(video_id).strip()
    for name in sorted(os.listdir(MP3_DIR)):
        if not name.lower().endswith('.mp3'):
            continue
        if name == video_id + '.mp3' or name.startswith(video_id + ' - '):
            return os.path.join(MP3_DIR, name)
    return None


def ffmpeg_available():
    """True when an ffmpeg binary can actually be found.

    yt-dlp converts the audio to MP3 itself, so a missing ffmpeg has to be
    reported rather than left to fail deep inside the download.
    """
    ffmpeg = find_ffmpeg()
    return bool(ffmpeg) and (os.path.isfile(ffmpeg) or bool(shutil.which(ffmpeg)))


def download_mp3(video_id, title=None, log=None, progress=None):
    """Download one video as a 192 kbps MP3 into MP3_DIR.

    Returns the file path, which is the existing file when the video has already
    been downloaded - the job is meant to be repeatable.  Raises RuntimeError
    with something readable when it cannot be done.
    """
    if yt_dlp is None:
        raise RuntimeError('yt-dlp is not installed; cannot download from YouTube.')

    video_id = str(video_id).strip()
    existing = mp3_path(video_id)
    if existing:
        return existing

    if not ffmpeg_available():
        raise RuntimeError('ffmpeg is required to turn downloads into MP3 - '
                           'install it or put a copy next to the app.')

    os.makedirs(MP3_DIR, exist_ok=True)

    # "<videoId> - <title>.mp3", sanitised here so the name matches what the
    # skip check looks for.  With no title of our own, yt-dlp fills it in.
    clean = sanitize_filename(title, '') if title else ''
    if clean:
        template = (video_id + ' - ' + clean).replace('%', '%%')  # % is a placeholder
    else:
        template = '%(id)s - %(title)s'

    ydlLogger = _YDLLogger()
    opts = _ytdlp_base_opts(logger=ydlLogger)
    opts.update({
        'format': 'bestaudio/best',
        'noplaylist': True,
        'socket_timeout': 30,
        'outtmpl': os.path.join(MP3_DIR, template + '.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    })

    if progress:
        # same throttle as the burner downloads: whole percents, twice a second
        state = {'percent': -1, 'at': 0.0}

        def report(status):
            if status.get('status') == 'finished':
                progress(100, 'Converting to MP3 ...')
                return
            if status.get('status') != 'downloading':
                return
            total = status.get('total_bytes') or status.get('total_bytes_estimate')
            done = status.get('downloaded_bytes') or 0
            if not total:
                return
            percent = int(done * 100 / total)
            now = time.time()
            if percent <= state['percent'] or now - state['at'] < 0.5:
                return
            state['percent'] = percent
            state['at'] = now
            progress(percent, 'Downloading {0}% of {1}'.format(
                percent, human_bytes(total)))

        opts['progress_hooks'] = [report]

    if log:
        log('Downloading: {0}'.format(title or video_id))

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info('https://www.youtube.com/watch?v=' + video_id,
                                    download=True)
    except Exception as e:
        raise RuntimeError(_bot_check_message(e))

    if not info:
        raise RuntimeError(_bot_check_message(
            ydlLogger.last_error() or 'YouTube returned no data'))

    # the postprocessor replaces the downloaded file, so look on disk rather
    # than trusting the extension yt-dlp reports
    path = mp3_path(video_id)
    if not path:
        raise RuntimeError('Download finished but no MP3 file was written.')

    if log:
        log('  downloaded {0} ({1})'.format(
            os.path.basename(path), human_bytes(os.path.getsize(path))))
    return path


class MusicHostSource(TrackSource):
    """Resolves and downloads audio from the /mp3 page's music host."""

    name = 'musichost'

    def __init__(self, base_url=None):
        self.base_url = base_url or get_music_base_url()
        self._index = None

    # -- index handling -------------------------------------------------------
    def _index_cache_path(self):
        return os.path.join(MUSIC_DIR, AUDIODB_NAME)

    def _load_index(self, force=False):
        """Return {relative path: {'seconds': int, 'bitrate': int}}."""
        if self._index is not None and not force:
            return self._index

        cache_path = self._index_cache_path()
        raw = None

        fresh = False
        if os.path.isfile(cache_path):
            age = time.time() - os.path.getmtime(cache_path)
            fresh = age < AUDIODB_MAX_AGE_SECONDS

        if fresh and not force:
            try:
                with open(cache_path, 'r', encoding='utf-8', errors='replace') as fp:
                    raw = fp.read()
            except Exception as e:
                print('acquire: could not read cached audiodb:', e)

        if raw is None:
            url = urllib.parse.urljoin(self.base_url, 'player/' + AUDIODB_NAME)
            try:
                data = http_get(url)
                raw = data.decode('utf-8', errors='replace')
                try:
                    os.makedirs(MUSIC_DIR, exist_ok=True)
                    with open(cache_path, 'w', encoding='utf-8') as fp:
                        fp.write(raw)
                except Exception as e:
                    print('acquire: could not cache audiodb:', e)
            except Exception as e:
                raise RuntimeError(
                    'Music host is unreachable ({0}): {1}'.format(url, e))

        index = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) < 5:
                continue
            full_path = parts[4].strip()
            if not full_path:
                continue
            try:
                seconds = float(parts[1])
            except (ValueError, IndexError):
                seconds = 0
            try:
                bitrate = int(float(parts[2]))
            except (ValueError, IndexError):
                bitrate = 0
            index[full_path] = {'seconds': seconds, 'bitrate': bitrate}

        self._index = index
        return index

    def _title_from_path(self, rel_path):
        base = os.path.basename(rel_path)
        base = os.path.splitext(base)[0]
        # library convention: "01-01-Artist-Title-LLS"
        base = base.replace('_', ' ')
        base = re.sub(r'\s+', ' ', base).strip()
        return base

    # -- TrackSource ----------------------------------------------------------
    def resolve(self, ref, log=None):
        """ref is the path of a file relative to the music host root."""
        ref = str(ref).strip().replace('\\', '/')
        index = self._load_index()
        info = index.get(ref)
        if info is None:
            raise RuntimeError('Not found in music library index: {0}'.format(ref))
        return [self.make_track(
            ref,
            self._title_from_path(ref),
            '',
            info['seconds'],
            info['bitrate'],
        )]

    def acquire(self, track, dest_dir, log=None, progress=None):
        rel = str(track['ref']).replace('\\', '/')
        dest = os.path.join(dest_dir, *rel.split('/'))

        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            return dest

        # Earlier versions staged these under the library tree.  A file left
        # there is reused rather than downloaded again - and because it lives in
        # MUSIC_DIR the burner leaves it in place instead of deleting it.
        if os.path.abspath(dest_dir) == os.path.abspath(MUSIC_SRC_DIR):
            legacy = os.path.join(MUSIC_DIR, *rel.split('/'))
            if os.path.isfile(legacy) and os.path.getsize(legacy) > 0:
                return legacy

        if log:
            log('Downloading: {0}'.format(rel))

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        url = self.base_url + urllib.parse.quote(rel)
        tmp = dest + '.part'
        try:
            data = http_get(url)
            with open(tmp, 'wb') as fp:
                fp.write(data)
            os.replace(tmp, dest)
        except Exception as e:
            if os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise RuntimeError('Could not download {0}: {1}'.format(url, e))
        if log:
            log('  downloaded {0} ({1})'.format(
                os.path.basename(dest), human_bytes(os.path.getsize(dest))))
        return dest


_PROVIDERS = {
    YouTubeSource.name: YouTubeSource,
    MusicHostSource.name: MusicHostSource,
}


def get_source(name):
    """Factory returning a TrackSource for a source name."""
    key = str(name or '').strip().lower()
    if key not in _PROVIDERS:
        raise ValueError('Unknown track source: {0!r}'.format(name))
    return _PROVIDERS[key]()


def source_dir(source_name):
    """Directory downloaded sources for a provider should live in.

    Sources are cached per provider rather than per project, so a track that has
    been downloaded but not yet converted is reused instead of fetched again.
    Both trees are cleared out again once the audio has been premastered.
    """
    if str(source_name or '').lower() == MusicHostSource.name:
        return MUSIC_SRC_DIR
    return MEDIA_DIR
