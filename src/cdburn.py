# -*- coding: utf-8 -*-
"""
CD burning core for DeckCastDJ.

Owns three things:
  * the CD project store (data/burner.json)
  * planning tracks onto one or more audio CDs
  * premastering to CD-ready WAV and handing the disc to the OS burner

Like acquire.py this module has no Flask dependency, so it can be driven from
the web app or from the command line:

    python cdburn.py plan    --url https://www.youtube.com/playlist?list=...
    python cdburn.py acquire --project guest@RoadTrip
    python cdburn.py premaster --project guest@RoadTrip
    python cdburn.py burn    --project guest@RoadTrip        # dry-run by default when no drive
    python cdburn.py burn    --project guest@RoadTrip --test # macOS: real burn, disc left blank
    python cdburn.py progress-demo                           # what a burn prints, no drive needed
    python cdburn.py clean   --project guest@RoadTrip --tier wav --dry-run

Only the native OS burners are used - drutil on macOS and IMAPI2 on Windows.
Neither writes CD-TEXT, so titles are carried in the WAV file names instead.

@author: Nathan
"""

import argparse
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import unicodedata

import acquire

# ---------------------------------------------------------------------------
# CD constants
# ---------------------------------------------------------------------------

# Red Book limits.
MAX_TRACKS = 99
MIN_TRACK_SECONDS = 4

# Usable audio seconds per blank, with a safety margin for lead-in/out and TOC.
# A "74 minute" CD-R really gives about 74:00 and an "80 minute" about 79:57;
# we leave a little headroom so a burn never overruns the disc.
USABLE_SECONDS = {
    74: 73 * 60 + 30,
    80: 79 * 60 + 30,
}
DEFAULT_BLANK = 80

PROJECTS_FILE = 'data/burner.json'
IMAPI2_SCRIPT = os.path.join(acquire.CD_DIR, 'burn_windows.ps1')

# ---------------------------------------------------------------------------
# CD project store  (data/burner.json)
# ---------------------------------------------------------------------------

PROJECTS = {}


def load_projects():
    """Load the project store, tolerating a missing or corrupt file."""
    global PROJECTS
    PROJECTS = {}
    if os.path.isfile(PROJECTS_FILE):
        try:
            with open(PROJECTS_FILE, 'r', encoding='utf-8') as fp:
                data = json.load(fp)
            if isinstance(data, dict):
                PROJECTS = data
        except Exception as e:
            print('cdburn: could not read {0}: {1}'.format(PROJECTS_FILE, e))
    return PROJECTS


def save_projects():
    try:
        os.makedirs(os.path.dirname(PROJECTS_FILE), exist_ok=True)
        with open(PROJECTS_FILE, 'w', encoding='utf-8') as fp:
            json.dump(PROJECTS, fp, indent=2)
    except Exception as e:
        print('cdburn: could not save {0}: {1}'.format(PROJECTS_FILE, e))


def project_key(username, name):
    return '{0}@{1}'.format((username or 'guest').lower().strip(), (name or '').strip())


def get_project(key):
    return PROJECTS.get(key)


def upsert_project(key, project):
    PROJECTS[key] = project
    save_projects()
    return project


def delete_project(key):
    if key in PROJECTS:
        PROJECTS.pop(key)
        save_projects()
        return True
    return False


def autosave_project(project, previous_name=None):
    """Keep a saved project in step with the working copy.

    Only projects the user has saved once are written - an unnamed working
    project stays in memory until Save is pressed.  Without this, tracks added
    after the first save were never persisted, so Load (or a restart) brought
    back an empty track list.  previous_name moves the saved record along when
    the project is renamed.
    """
    key = project_key(project.get('username'), project.get('name'))
    old_key = project_key(project.get('username'), previous_name) if previous_name else None
    if key not in PROJECTS and (not old_key or old_key not in PROJECTS):
        return False
    if old_key and old_key != key and PROJECTS.get(old_key) is project:
        PROJECTS.pop(old_key, None)
    upsert_project(key, project)
    return True


def list_projects(username):
    """Projects belonging to a username, keyed by the bare project name."""
    prefix = (username or '').lower().strip() + '@'
    out = {}
    for key, project in PROJECTS.items():
        if key.startswith(prefix):
            out[key[len(prefix):]] = project
    return out


def new_project(name, username='guest', blank=DEFAULT_BLANK):
    return {
        'name': name,
        'username': (username or 'guest').lower().strip(),
        'target': 'audio',
        'blank': int(blank),
        'createdAt': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'status': 'planned',
        'discs': [],
        'tracks': [],
    }


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def format_hms(seconds):
    seconds = int(round(max(0, seconds or 0)))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours:
        return '{0}:{1:02d}:{2:02d}'.format(hours, minutes, secs)
    return '{0}:{1:02d}'.format(minutes, secs)


def usable_seconds(blank=DEFAULT_BLANK):
    blank = int(blank or DEFAULT_BLANK)
    if blank in USABLE_SECONDS:
        return USABLE_SECONDS[blank]
    # any other size: treat the number as whole minutes with the same margin
    return blank * 60 - 30


def plan(tracks, blank=DEFAULT_BLANK):
    """Lay tracks out across as many discs as needed.

    Returns (discs, warnings).  Each disc is a dict with an index, the list of
    tracks on it, the total seconds and the track count.  Tracks are annotated
    in place with discIndex and trackNo.
    """
    limit = usable_seconds(blank)
    warnings = []
    discs = []

    current = {'index': 1, 'tracks': [], 'totalSeconds': 0, 'trackCount': 0}

    for track in tracks:
        seconds = float(track.get('seconds') or 0)
        if seconds and seconds < MIN_TRACK_SECONDS:
            warnings.append('"{0}" is shorter than {1}s and may be rejected by '
                            'some burners.'.format(track.get('title'), MIN_TRACK_SECONDS))

        # start a new disc if this one is full (but never emit an empty disc)
        is_full = (current['totalSeconds'] + seconds > limit
                   or current['trackCount'] >= MAX_TRACKS)
        if is_full and current['tracks']:
            discs.append(current)
            current = {'index': current['index'] + 1, 'tracks': [],
                       'totalSeconds': 0, 'trackCount': 0}

        if seconds > limit:
            warnings.append('"{0}" is longer than a whole disc ({1}) and will '
                            'not fit.'.format(track.get('title'), format_hms(limit)))

        track['discIndex'] = current['index']
        track['trackNo'] = current['trackCount'] + 1
        current['tracks'].append(track)
        current['totalSeconds'] += seconds
        current['trackCount'] += 1

    if current['tracks']:
        discs.append(current)

    for disc in discs:
        disc['totalSeconds'] = int(round(disc['totalSeconds']))
        disc['limitSeconds'] = limit
        disc['fill'] = (disc['totalSeconds'] / float(limit)) if limit else 0

    return discs, warnings


def _track_signature(track):
    """What a track contributes to "this disc still holds the same audio"."""
    return (track.get('source'), track.get('ref'), track.get('title'))


def apply_plan(project, blank=None):
    """Recompute the disc layout for a project and store it on the project.

    A disc that has already been written keeps its "burned" mark when the new
    plan still lays out exactly the same tracks on it - otherwise loading a
    saved project would forget which discs are done.  Any other change drops
    the mark, because that disc needs writing again.
    """
    if blank is None:
        blank = project.get('blank', DEFAULT_BLANK)
    project['blank'] = int(blank)

    burned = {}
    for disc in project.get('discs', []):
        if disc.get('burnedAt'):
            burned[int(disc.get('index', 0))] = (
                disc['burnedAt'],
                [_track_signature(t) for t in disc.get('tracks', [])],
            )

    discs, warnings = plan(project.get('tracks', []), blank)
    for disc in discs:
        previous = burned.get(int(disc.get('index', 0)))
        if previous and previous[1] == [_track_signature(t)
                                       for t in disc.get('tracks', [])]:
            disc['burnedAt'] = previous[0]
    project['discs'] = discs

    if project.get('status') in (None, 'planned'):
        project['status'] = 'planned'
    elif project.get('status') == 'burned' and (
            not discs or not all(d.get('burnedAt') for d in discs)):
        # the layout moved on, so the set is not fully written any more
        project['status'] = 'planned'
    return discs, warnings


def project_summary(project):
    tracks = project.get('tracks', [])
    total = sum(float(t.get('seconds') or 0) for t in tracks)
    discs = project.get('discs', [])
    return {
        'trackCount': len(tracks),
        'maxTracks': MAX_TRACKS,
        'totalSeconds': int(round(total)),
        'totalTime': format_hms(total),
        'discCount': len(discs),
        'limitSeconds': usable_seconds(project.get('blank', DEFAULT_BLANK)),
        'limitTime': format_hms(usable_seconds(project.get('blank', DEFAULT_BLANK))),
    }


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def project_dir_for_name(name):
    """Media folder for a project name - the folder follows the project name."""
    return os.path.join(acquire.CD_DIR, acquire.sanitize_filename(name, 'project'))


def project_dir(project):
    return project_dir_for_name(project.get('name'))


def _move_into(src, dst):
    """Move a file or folder onto dst, keeping whatever is already there."""
    if os.path.isdir(src):
        os.makedirs(dst, exist_ok=True)
        for entry in os.listdir(src):
            _move_into(os.path.join(src, entry), os.path.join(dst, entry))
        try:
            os.rmdir(src)
        except OSError:
            pass
        return
    if os.path.exists(dst):
        # same disc and track number, so this is the same premaster - keep one
        os.remove(src)
    else:
        os.replace(src, dst)


def rename_project_folder(old_name, new_name):
    """Move a project's media folder after the project was renamed."""
    old_dir = project_dir_for_name(old_name)
    new_dir = project_dir_for_name(new_name)
    if os.path.abspath(old_dir) == os.path.abspath(new_dir):
        return False
    if not os.path.isdir(old_dir):
        return False
    os.makedirs(new_dir, exist_ok=True)
    for entry in os.listdir(old_dir):
        _move_into(os.path.join(old_dir, entry), os.path.join(new_dir, entry))
    try:
        os.rmdir(old_dir)
    except OSError:
        pass
    return True


def set_project_name(project, new_name, log=None):
    """Rename a project and take its downloaded audio with it.

    Premastered WAVs live under the folder named after the project, so renaming
    without moving would strand them and every track would look like it still
    needed downloading.
    """
    new_name = str(new_name or '').strip()
    old_name = project.get('name')
    if not new_name or new_name == old_name:
        return False
    project['name'] = new_name
    try:
        moved = rename_project_folder(old_name, new_name)
    except OSError as e:
        if log:
            log('Could not move the media folder: {0}'.format(e), 'err')
        return False
    if moved and log:
        log('Moved downloaded audio to "{0}".'.format(new_name))
    return moved


def disc_dir(project, disc_index):
    return os.path.join(project_dir(project), 'disc{0}'.format(int(disc_index)))


def _renumber_files(moves, log=None):
    """Rename/relocate premastered WAVs, staged first so a swap cannot clash."""
    staged = []
    try:
        for old, new in moves:
            os.makedirs(os.path.dirname(new), exist_ok=True)
            tmp = old + '.renumber'
            os.replace(old, tmp)
            staged.append((tmp, old, new))
        for tmp, _old, new in staged:
            os.replace(tmp, new)
    except OSError as e:
        for tmp, old, _new in staged:
            try:
                if os.path.exists(tmp):
                    os.replace(tmp, old)
            except OSError:
                pass
        if log:
            log('Could not renumber the premastered files: {0}'.format(e), 'err')
        return False
    if log:
        log('Renumbered {0} premastered file(s) to match the new order.'.format(
            len(staged)))
    return True


# What premastering writes, and the extensions a folder burn will pick up as
# audio on top of it.  Windows (and the macOS fallback) burn the folder itself,
# and drutil converts "any valid QuickTime audio file" in the folder it is given,
# so a stray source file left in there becomes a track.
WAV_EXTENSION = '.wav'
STRAY_AUDIO_EXTENSIONS = ('.aif', '.aiff', '.aifc', '.caf', '.m4a', '.m4b', '.mp3',
                          '.mp4', '.aac', '.au', '.snd', '.sd2', '.w64', '.flac',
                          '.ogg', '.opus')

# The layout file a macOS burn is handed instead of the disc folder.  It holds no
# audio, so it is neither a track nor one of the stray-audio files above.
TOC_NAME = 'DeckCastDJ.toc'


def disc_tracks(project, disc_index):
    """The tracks of one disc, in the order the disc should have."""
    for disc in project.get('discs', []):
        if int(disc.get('index', 0)) == int(disc_index):
            return list(disc.get('tracks') or [])
    return []


def disc_order(project, disc_index):
    """The file names that make up a disc, in the order the disc should have.

    An audio CD's track order is decided by the premastered names - "NN - Artist
    - Title.wav", numbered per disc and zero padded - on Windows, where the
    burner script sorts them by name.  On macOS the same names are written out as
    a .toc layout (see audio_toc_text) rather than being left for drutil to find
    in a folder: drutil's man page says a directory is burned "in alphabetical
    order", but a directory is walked in the file system's own order, which is
    how a disc came out with the page's track 8 first.
    """
    return [premaster_name(t) for t in disc_tracks(project, disc_index)]


def _same_name(name):
    """A file name in the form both sides can be compared in.

    macOS hands names back decomposed ("Me" + accent) where the plan holds the
    composed spelling, so a title with an accent in it would look like a file the
    plan does not know - and a planned file would look missing.
    """
    return unicodedata.normalize('NFC', name or '')


def _folder_mismatch(disc_folder, expected):
    """(missing, extra, stray) - how the burner's folder differs from the plan.

    The whole folder is what gets burned, so any file in it that the plan does
    not know about becomes a track of its own and shoves every track after it
    one place along.  Leftovers from an earlier layout are exactly how a disc
    ends up starting with the wrong track 1, which is why they are worth
    refusing over rather than burning.
    """
    try:
        names = os.listdir(disc_folder)
    except OSError:
        names = []
    have = {}
    for name in names:
        have.setdefault(_same_name(name), name)
    want = {_same_name(name): name for name in expected}
    missing = [name for key, name in want.items() if key not in have]
    extra = sorted(have[key] for key in have
                   if key not in want and key.lower().endswith(WAV_EXTENSION))
    stray = sorted(have[key] for key in have
                   if key not in want and key.lower().endswith(STRAY_AUDIO_EXTENSIONS))
    return missing, extra, stray


def _describe_folder_problem(disc_index, missing, extra, stray):
    """One line for the page, with the detail now in the log."""
    parts = []
    if missing:
        parts.append('{0} planned track(s) missing'.format(len(missing)))
    if extra:
        parts.append('{0} file(s) that are not on the list'.format(len(extra)))
    if stray:
        parts.append('{0} other audio file(s) in there'.format(len(stray)))
    if not parts:
        parts.append('its file names do not sort into the planned order')
    return ('Disc {0} was not burned: what is in the folder is not what the page '
            'lists ({1}), so the disc would not play the tracks in this order. Run '
            '1 - Acquire Audio (it renumbers the files and clears out anything the '
            'list no longer uses).'.format(disc_index, ', '.join(parts)))


def _toc_quote(value):
    """A path the way a cdrdao .toc file wants it."""
    return value.replace('\\', '\\\\').replace('"', '\\"')


def audio_toc_text(disc_folder, names):
    """The disc's own layout: every track named, in the order it plays.

    This is the cdrdao .toc form, which is the form drutil takes for an audio
    disc ("If a file is specified (valid image files only: .dmg, .iso, .cue/bin,
    and .toc) the contents of the image file are burned").  Handing drutil the
    layout instead of the folder means the order no longer depends on how it
    walks a directory - which is the one thing that made the disc's track 1 not
    the page's track 1 - and it also means only these files are burned, so a
    leftover in the folder cannot become a track.

    Written in UTF-8 (a title can hold an accent) with plain \\n endings, and
    with the files named by full path so the layout still describes the same
    files if it is read from somewhere else.
    """
    lines = ['CD_DA', '']
    for name in names:
        path = os.path.abspath(os.path.join(disc_folder, name))
        lines.append('TRACK AUDIO')
        lines.append('FILE "{0}" 0'.format(_toc_quote(path)))
        lines.append('')
    return '\n'.join(lines)


def write_audio_toc(disc_folder, names, toc_path=None):
    """Write the layout beside the audio; returns the path it wrote."""
    toc_path = toc_path or os.path.join(disc_folder, TOC_NAME)
    with open(toc_path, 'w', encoding='utf-8', newline='\n') as fp:
        fp.write(audio_toc_text(disc_folder, names))
    return toc_path


# Both burners are handed 44.1kHz/16-bit/stereo PCM, and both read the WAV
# chunks to find the audio: the Windows script walks the chunks and pads the
# samples out to whole sectors, drutil converts the file itself.  A file that
# does not hold what its header claims is not worth burning - it is how a disc
# ends up with fewer tracks than the page lists, with nothing saying why.
_AUDIO_FORMAT_PCM = 1


def _wav_chunks(path):
    """(fmt fields, (data_offset, data_size), reason) - reason set when unreadable."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as fp:
            head = fp.read(12)
            if len(head) < 12 or head[:4] != b'RIFF' or head[8:12] != b'WAVE':
                return None, None, 'not a WAV file (no RIFF/WAVE header)'
            fmt = None
            pos = 12
            while pos + 8 <= size:
                fp.seek(pos)
                chunk = fp.read(8)
                if len(chunk) < 8:
                    break
                name = chunk[:4]
                length = int.from_bytes(chunk[4:8], 'little')
                body = pos + 8
                if name == b'fmt ':
                    raw = fp.read(16)
                    if len(raw) < 16:
                        return None, None, 'the fmt chunk is truncated'
                    fmt = {
                        'format': int.from_bytes(raw[0:2], 'little'),
                        'channels': int.from_bytes(raw[2:4], 'little'),
                        'rate': int.from_bytes(raw[4:8], 'little'),
                        'bits': int.from_bytes(raw[14:16], 'little'),
                    }
                elif name == b'data':
                    if fmt is None:
                        return None, None, 'the data chunk comes before the fmt chunk'
                    if length <= 0:
                        return None, None, 'it holds no audio'
                    if body + length > size:
                        return None, None, ('it was not downloaded in full ({0} of {1} '
                                            'bytes of audio)'.format(size - body, length))
                    return fmt, (body, length), None
                pos = body + length + (length & 1)
            return None, None, 'it has no audio (data chunk)'
    except OSError as e:
        return None, None, 'it could not be read ({0})'.format(e)


def wav_problem(path):
    """Why a WAV cannot be burned as a track, or None if it can.

    Only what both burners need is insisted on: PCM, 44.1kHz, 2 channels, 16
    bits, and an audio chunk that is really there.
    """
    fmt, _data, reason = _wav_chunks(path)
    if reason:
        return reason
    if fmt['format'] != _AUDIO_FORMAT_PCM:
        return ('it is not uncompressed PCM audio (format {0}), so it would have '
                'to be converted first'.format(fmt['format']))
    wrong = []
    if fmt['rate'] != 44100:
        wrong.append('{0} Hz'.format(fmt['rate']))
    if fmt['channels'] != 2:
        wrong.append('{0} channel(s)'.format(fmt['channels']))
    if fmt['bits'] != 16:
        wrong.append('{0}-bit'.format(fmt['bits']))
    if wrong:
        return 'it is not 44.1kHz 16-bit stereo (it says {0})'.format(', '.join(wrong))
    return None


def unusable_tracks(disc_folder, names):
    """[(name, why)] for the planned tracks that cannot be burned as they are."""
    bad = []
    for name in names:
        path = os.path.join(disc_folder, name)
        problem = wav_problem(path) if os.path.isfile(path) else 'it is not there'
        if problem:
            bad.append((name, problem))
    return bad


def premaster_paths(project):
    """{id(track): path} for tracks whose premastered WAV exists right now."""
    out = {}
    for track in project.get('tracks') or []:
        path = premaster_path(project, track)
        if path and os.path.isfile(path):
            out[id(track)] = path
    return out


def carry_premasters(project, before, log=None):
    """Move premastered WAVs to the paths the re-planned project expects.

    Track order sets the disc layout and the file name carries the track
    number, so anything that renumbers tracks - reordering, sorting, removing or
    changing the blank - has to renumber the files too.  Otherwise every
    premastered track would look like it still needed downloading.
    """
    moves = []
    for track in project.get('tracks') or []:
        old = before.get(id(track))
        if not old:
            continue
        new = premaster_path(project, track)
        if new and os.path.abspath(new) != os.path.abspath(old):
            moves.append((old, new))
    if not moves:
        return False
    return _renumber_files(moves, log=log)


def move_track(project, index, delta, log=None):
    """Move one track up or down, taking its premastered WAV with it."""
    tracks = project.get('tracks') or []
    try:
        index = int(index)
        target = index + (-1 if int(delta) < 0 else 1)
    except (TypeError, ValueError):
        return False
    if not (0 <= index < len(tracks)) or not (0 <= target < len(tracks)):
        return False

    before = premaster_paths(project)
    tracks[index], tracks[target] = tracks[target], tracks[index]
    apply_plan(project)
    carry_premasters(project, before, log=log)
    return True


def remove_unpremastered(project, log=None):
    """Drop every track with no CD-ready WAV.  Returns the tracks removed.

    Some downloads never arrive: the video is blocked by copyright or by a region
    lock, or the conversion fails.  Such a track can never be burned, but it still
    holds a track number, a disc and a place in the burn order, so it keeps
    turning up in the plan and in the folder checks.  This is the tidy-up: every
    track that has audio stays, in the same order, and the discs close up around
    the gaps (files included, so names and track numbers still agree).

    A downloaded original that never became a WAV goes with its track, so a retry
    starts clean - but only files this feature staged are ever deleted, as in
    _discard_staged_source.
    """
    tracks = project.get('tracks') or []
    going = [t for t in tracks if not is_premastered(project, t)]
    if not going:
        return []

    before = premaster_paths(project)
    project['tracks'] = [t for t in tracks if is_premastered(project, t)]
    freed = 0
    discarded = 0
    for track in going:
        gone_bytes = _discard_staged_source(track, _resolve_source(track))
        if gone_bytes:
            freed += gone_bytes
            discarded += 1
    apply_plan(project)
    carry_premasters(project, before, log=log)

    if log:
        log('Removed {0} track(s) with no downloaded audio: {1}'.format(
            len(going),
            ', '.join(str(t.get('title') or t.get('ref') or '?') for t in going)))
        if discarded:
            log('Deleted {0} downloaded original(s) with them, freed {1}.'.format(
                discarded, acquire.human_bytes(freed)))
        log('{0} track(s) left; the discs have been replanned around them.'.format(
            len(project['tracks'])))
    return going


def premaster_name(track):
    """File name used for a premastered track (also defines burn order)."""
    number = int(track.get('trackNo') or 1)
    title = acquire.sanitize_filename(track.get('title'), 'Track {0}'.format(number))
    artist = acquire.sanitize_filename(track.get('artist'), '') if track.get('artist') else ''
    if artist and artist.lower() not in title.lower():
        return '{0:02d} - {1} - {2}.wav'.format(number, artist, title)
    return '{0:02d} - {1}.wav'.format(number, title)


def premaster_path(project, track):
    """Where this track's CD-ready WAV lives, or None if it is not on a disc."""
    disc_index = track.get('discIndex')
    if not disc_index:
        return None
    return os.path.join(disc_dir(project, disc_index), premaster_name(track))


def is_premastered(project, track):
    """True when a usable WAV already exists for this track."""
    path = premaster_path(project, track)
    return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 44


def premastered_count(project):
    """How many of the project's tracks already have a CD-ready WAV."""
    return sum(1 for track in project.get('tracks') or [] if is_premastered(project, track))


# ---------------------------------------------------------------------------
# Acquire / premaster
# ---------------------------------------------------------------------------


def acquire_project(project, progress=None, log=None):
    """Download every source a project still needs.  Returns (ok, errors).

    Tracks whose CD-ready WAV already exists are skipped: the downloaded
    original is deleted as soon as its WAV is written, so without this a
    second run would re-download work that has already been finished.
    """
    errors = []
    tracks = project.get('tracks', [])
    skipped = 0

    if log and tracks:
        log('Checking {0} track(s) for audio that still needs downloading ...'
            .format(len(tracks)))

    for i, track in enumerate(tracks):
        if progress:
            progress(i, len(tracks), track)

        if is_premastered(project, track):
            skipped += 1
            continue

        dest_dir = acquire.source_dir(track.get('source'))
        try:
            source = acquire.get_source(track.get('source'))

            def on_progress(percent, text, _i=i, _track=track):
                if progress:
                    progress(_i, len(tracks), _track, text)

            path = source.acquire(track, dest_dir, log=log, progress=on_progress)
            track['srcFile'] = path
            if not track.get('seconds'):
                probed = probe_seconds(path)
                if probed:
                    track['seconds'] = probed
        except Exception as e:
            errors.append('{0}: {1}'.format(track.get('title', track.get('ref')), e))

    if progress:
        progress(len(tracks), len(tracks), None)
    if log and skipped:
        log('{0} track(s) are already premastered - skipped.'.format(skipped))
    if not errors:
        project['status'] = 'acquired'
        apply_plan(project)
    return (not errors), errors


def probe_seconds(path):
    """Best-effort media duration in seconds using ffprobe."""
    ffprobe = shutil.which('ffprobe')
    if not ffprobe or not path or not os.path.isfile(path):
        return 0
    try:
        out = subprocess.run(
            [ffprobe, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=nw=1:nk=1', path],
            capture_output=True, text=True, timeout=60)
        return int(round(float(out.stdout.strip())))
    except Exception:
        return 0


def premaster_disc(project, disc_index, progress=None, log=None):
    """Convert every track on a disc to CD-ready 16-bit/44.1kHz/stereo WAV."""
    discs = project.get('discs', [])
    disc = next((d for d in discs if int(d.get('index', 0)) == int(disc_index)), None)
    if disc is None:
        raise RuntimeError('Disc {0} does not exist in this project.'.format(disc_index))

    out_dir = disc_dir(project, disc_index)
    os.makedirs(out_dir, exist_ok=True)
    ffmpeg = acquire.find_ffmpeg()

    errors = []
    tracks = disc.get('tracks', [])
    discarded = 0
    freed = 0

    # drutil burns a directory (the Windows script and the macOS fallback both
    # burn the folder), so make sure it only contains this disc's current
    # tracks.  Leftovers from an earlier layout - or any other audio that found
    # its way in there - would otherwise be burned by accident, as extra tracks,
    # shoving everything else along.
    expected = set(_same_name(premaster_name(t)) for t in tracks)
    cleared = []
    for name in os.listdir(out_dir):
        lower = name.lower()
        if _same_name(name) == _same_name(TOC_NAME):
            # the disc layout a burn writes describes the tracks as they were;
            # the plan has just changed, so it is not this disc's layout any more
            try:
                os.remove(os.path.join(out_dir, name))
                cleared.append(name)
            except OSError:
                pass
            continue
        if _same_name(name) in expected:
            continue
        if not (lower.endswith(WAV_EXTENSION) or lower.endswith(STRAY_AUDIO_EXTENSIONS)):
            continue
        try:
            os.remove(os.path.join(out_dir, name))
            cleared.append(name)
        except OSError:
            pass
    if cleared and log:
        log('Disc {0}: deleted {1} file(s) an earlier layout left behind - {2}'
            .format(disc_index, len(cleared), ', '.join(sorted(cleared))))

    for i, track in enumerate(tracks):
        if progress:
            progress(i, len(tracks), track)

        out_path = os.path.join(out_dir, premaster_name(track))

        if os.path.isfile(out_path) and os.path.getsize(out_path) > 44:
            # already converted on an earlier run, so there is nothing to do
            # but clear out any original that is still lying around
            track['wavFile'] = out_path
            removed = _discard_staged_source(track, _resolve_source(track))
            if removed is not None:
                discarded += 1
                freed += removed
            continue

        src = _resolve_source(track)
        if not src:
            errors.append('{0}: source file is missing - run Acquire Audio '
                          'first.'.format(track.get('title', track.get('ref'))))
            continue

        try:
            subprocess.run(
                [ffmpeg, '-y', '-loglevel', 'error', '-i', src,
                 '-ar', '44100', '-ac', '2', '-c:a', 'pcm_s16le',
                 '-f', 'wav', out_path],
                check=True, capture_output=True, text=True)
            track['wavFile'] = out_path
        except subprocess.CalledProcessError as e:
            # keep the original: re-downloading it would not fix a ffmpeg
            # failure, so it is left in place for a retry
            detail = (e.stderr or '').strip().splitlines()
            errors.append('{0}: ffmpeg failed{1}'.format(
                track.get('title', track.get('ref')),
                ' (' + detail[-1] + ')' if detail else ''))
            continue
        except Exception as e:
            errors.append('{0}: {1}'.format(track.get('title', track.get('ref')), e))
            continue

        # the WAV is all the burn needs, so the original is never kept
        removed = _discard_staged_source(track, src)
        if removed is not None:
            discarded += 1
            freed += removed

    if progress:
        progress(len(tracks), len(tracks), None)
    if log and discarded:
        log('Deleted {0} downloaded original(s), freed {1}.'.format(
            discarded, acquire.human_bytes(freed)))
    if discarded:
        # a music-host download is stored under the library's own subfolders, so
        # deleting it can leave the album folders behind
        _prune_empty_dirs(acquire.MUSIC_SRC_DIR, log=log)
    if not errors:
        project['status'] = 'premastered'
    return (not errors), errors


def _resolve_source(track):
    """Local path of a track's downloaded original, if it is still there."""
    path = track.get('srcFile')
    if path and os.path.isfile(path):
        return path
    return _find_source_on_disk(track)


def _discard_staged_source(track, path):
    """Delete a downloaded original once its WAV exists.

    Only files this feature staged are removed: music-host downloads land in
    MUSIC_SRC_DIR, and the /mp3 page's music library plus the cached audiodb
    index beside it stay untouched because nothing in MUSIC_DIR is ever deleted
    automatically.  Returns the bytes removed, or None when nothing was deleted.
    """
    if not path:
        return None
    try:
        full = os.path.abspath(path)
        media = os.path.abspath(acquire.MEDIA_DIR)
        music = os.path.abspath(acquire.MUSIC_DIR)
        if os.path.commonpath([full, music]) == music:
            return None
        if os.path.commonpath([full, media]) != media:
            return None
        size = os.path.getsize(full)
        os.remove(full)
    except (OSError, ValueError):
        return None
    track['srcFile'] = None
    return size


def _find_source_on_disk(track):
    """Recover a source path when the stored one is stale."""
    source_name = track.get('source')
    ref = str(track.get('ref') or '')
    if source_name == acquire.MusicHostSource.name:
        # MUSIC_DIR is where they were staged before the burner had its own
        # tree, so an old project still resolves to a file that is already there
        for tree in (acquire.MUSIC_SRC_DIR, acquire.MUSIC_DIR):
            candidate = os.path.join(tree, *ref.replace('\\', '/').split('/'))
            if os.path.isfile(candidate):
                return candidate
        return None
    for ext in ('webm', 'm4a', 'mp3', 'opus', 'ogg', 'wav', 'mp4'):
        candidate = os.path.join(acquire.MEDIA_DIR, '{0}_src.{1}'.format(ref, ext))
        if os.path.isfile(candidate):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Burn backends
# ---------------------------------------------------------------------------


def list_devices():
    """Return a list of available burners on this machine.

    Each entry is a dict with at least 'backend', 'id' and 'name'.
    """
    devices = []
    if sys.platform == 'darwin':
        try:
            out = subprocess.run(['drutil', 'list'], capture_output=True,
                                 text=True, timeout=30)
            for line in (out.stdout or '').splitlines():
                line = line.strip()
                # drutil lists one device per line, typically "1  ...  <name>"
                if not line or 'Vendor' in line or 'Model' in line:
                    continue
                if re.match(r'^\d+\s', line):
                    devices.append({
                        'backend': 'drutil',
                        'id': line.split()[0],
                        'name': line,
                    })
        except FileNotFoundError:
            pass
        except Exception as e:
            print('cdburn: drutil list failed:', e)
    elif os.name == 'nt':
        devices.extend(_imapi2_devices())
    return devices


def media_status():
    """Return a short human-readable status of the inserted media."""
    if sys.platform == 'darwin':
        try:
            out = subprocess.run(['drutil', 'status'], capture_output=True,
                                 text=True, timeout=30)
            text = (out.stdout or '').strip()
            blank = 'Blank' if 'Blank' in text else None
            if 'No Media Inserted' in text or not text:
                return {'present': False, 'blank': False, 'text': text or 'No media'}
            return {'present': True, 'blank': bool(blank), 'text': text}
        except FileNotFoundError:
            return {'present': False, 'blank': False,
                    'text': 'drutil not found (not macOS?)'}
        except Exception as e:
            return {'present': False, 'blank': False, 'text': str(e)}
    if os.name == 'nt':
        devs = _imapi2_devices()
        if not devs:
            return {'present': False, 'blank': False, 'text': 'No optical drive found'}
        return {'present': True, 'blank': None,
                'text': devs[0].get('media', 'Unknown media state')}
    return {'present': False, 'blank': False, 'text': 'Unsupported platform'}


def burn_disc(project, disc_index, dry_run=False, verify=True, device=None, speed=None,
              log=None, progress=None, test_burn=False, check_only=False):
    """Burn one disc of a project.  Returns a result dict.

    When dry_run is set (or no drive is present) nothing is written; the exact
    command that would run is returned instead.  log, when given, receives the
    burner's own output line by line while the disc is being written, and
    progress receives (percent, 100, line) as the burner reports a new
    percentage - which is what drives the burner page's progress bar.

    test_burn asks the drive to go through the whole burn without writing, which
    is how the progress output can be checked without spending a disc.  A test
    never turns into a real burn: where the drive or the platform cannot do one
    (no disc inserted, or Windows, where IMAPI2 can only really write) the burn
    is taken as far as it can go without the drive - the audio is checked, or
    the command that would run is reported - with the reason.
    """
    disc_folder = disc_dir(project, disc_index)
    if not os.path.isdir(disc_folder):
        return _result(False, dry_run, 'No premastered files for disc {0}. '
                        'Run premaster first.'.format(disc_index))

    wavs = sorted(f for f in os.listdir(disc_folder) if f.lower().endswith('.wav'))
    if not wavs:
        return _result(False, dry_run, 'Disc {0} has no WAV files.'.format(disc_index))

    # What is in the folder has to be exactly the tracks the page lists: the
    # folder is what a burn is taken from, so a file the page does not know about
    # is a track the page does not show - and a file whose name does not match the
    # page's track number is the wrong song in that place.  Both have happened:
    # a disc whose track 1 was the page's track 8 came from a folder left over
    # from an earlier layout.  Checked for a test burn too - that is the point of
    # one - and before the drive is touched.
    expected = disc_order(project, disc_index)
    missing, extra, stray = _folder_mismatch(disc_folder, expected)
    unsorted = bool(expected) and expected != sorted(expected)
    if missing or extra or stray or unsorted or not expected:
        if log:
            log('Disc {0} does not match the plan as it stands:'.format(disc_index))
            for name in missing:
                log('  tracked on the page, but not in the folder: ' + name)
            for name in extra:
                log('  in the folder, but not on the page\'s list: ' + name)
            for name in stray:
                log('  other audio in the folder: ' + name)
            if unsorted:
                log('  the planned file names do not sort into the planned order: '
                    + ', '.join(expected[:4]))
            log('Nothing was burned.  A disc burned from a folder the plan does not '
                'match has the wrong tracks in the wrong places, so the folder is '
                'checked against the page\'s list first.  Run 1 - Acquire Audio to '
                'make disc {0} match the list again: it renumbers the files, keeps '
                'the audio that is already downloaded and clears out the rest.'
                .format(disc_index))
        return _result(False, dry_run,
                       _describe_folder_problem(disc_index, missing, extra, stray))

    if log:
        # the disc's own table of contents, printed before anything is written:
        # track 1 is the file that sorts first
        log('The disc\'s track order (this is what is burned):')
        for number, name in enumerate(expected, 1):
            log('  {0:2}. {1}'.format(number, name))

    # A file the burners cannot read is not burned and nothing says so: the disc
    # comes out with tracks missing and the log looks exactly like a good burn.
    # Checked here, before the drive is touched, so the file is named instead.
    bad = unusable_tracks(disc_folder, expected)
    if bad:
        if log:
            log('Disc {0} has track(s) that cannot be burned as they are:'.format(
                disc_index))
            for name, problem in bad:
                log('  {0}: {1}'.format(name, problem))
            log('Nothing was burned.  The audio for those track(s) has to be '
                'downloaded again: remove the file(s) above and run 1 - Acquire '
                'Audio.')
        return _result(False, dry_run,
                       'Disc {0} was not burned: {1} of its {2} track(s) cannot be '
                       'burned ({3}). Remove and download again.'.format(
                           disc_index, len(bad), len(expected), bad[0][1]))

    if check_only:
        # the Windows burner needs the audio dug out of the WAVs and padded out
        # to whole sectors, so there is something worth checking there; drutil
        # takes the files as they are, so on macOS the two things a burn depends
        # on - the folder matching the page, and the audio in the files - are what
        # has just been checked
        if sys.platform == 'darwin':
            if log:
                log('{0} track(s) checked: the folder matches the page\'s list, and '
                    'every file holds 44.1kHz 16-bit stereo PCM in full.'
                    .format(len(expected)))
            return _result(True, True, 'Disc {0} is ready to burn ({1} track(s)).'
                           .format(disc_index, len(expected)))
        if os.name == 'nt':
            return _burn_imapi2(disc_folder, wavs, True, verify, device, log=log,
                                progress=progress, check_only=True)
        return _result(True, True, 'Disc {0} is ready to burn ({1} track(s)).'
                       .format(disc_index, len(expected)))

    if sys.platform == 'darwin':
        if test_burn and not media_status().get('present'):
            # a test burn is the same drive work as the real thing, so with
            # nothing in the drive there is no burn to test - say so and show
            # the command a test burn would run
            result = _burn_drutil(disc_folder, True, verify, device, speed, log=log,
                                  progress=progress, test_burn=True, expected=expected)
            result['message'] = ('Nothing to test with - insert a blank disc '
                                 'first.  Nothing was written.')
            return result
        return _burn_drutil(disc_folder, dry_run, verify, device, speed, log=log,
                            progress=progress, test_burn=test_burn, expected=expected)
    if os.name == 'nt':
        if test_burn:
            # IMAPI2 has no test mode and a test must never turn into a real
            # burn, so Windows runs the script's check instead: everything a
            # burn does to the audio, with the drive left alone
            if log:
                log('Windows has no test burn (IMAPI2 can only really write), so '
                    'the audio is checked instead - the drive is not touched.')
            return _burn_imapi2(disc_folder, wavs, True, verify, device, log=log,
                                progress=progress, check_only=True)
        return _burn_imapi2(disc_folder, wavs, dry_run, verify, device, log=log,
                            progress=progress)
    return _result(False, dry_run, 'CD burning is not supported on this platform.')


def _result(success, dry_run, message, **kwargs):
    res = {'success': bool(success), 'dryRun': bool(dry_run), 'message': message}
    res.update(kwargs)
    return res


# ---------------------------------------------------------------------------
# Running the OS burner
# ---------------------------------------------------------------------------

# Bytes of CD audio per second at 1x, and the WAV header premastering writes.
# Every WAV handed to a burner is 44.1kHz/16-bit/stereo, so payload bytes and
# playing time convert both ways with these two numbers.
AUDIO_BYTES_PER_SECOND = 44100 * 2 * 2
WAV_HEADER_BYTES = 44

_NO_ITEM = object()

# drutil announces the track it is writing ("Writing track 3 ..."), which is
# what the burner page's label is built from; the Windows burner prints a line
# per track instead.
_TRACK_LINE = re.compile(r'writing\s+track\s+(\d+)', re.IGNORECASE)


def _disc_audio(names, folder):
    """(audio seconds, PCM bytes) for a folder of premastered WAVs."""
    total = 0
    for name in names:
        try:
            total += max(0, os.path.getsize(os.path.join(folder, name)) - WAV_HEADER_BYTES)
        except OSError:
            pass
    return total / float(AUDIO_BYTES_PER_SECOND), total


def _speed_note(payload_bytes, elapsed):
    """How fast the drive actually wrote, in CD x-speeds."""
    if not payload_bytes or elapsed <= 0:
        return ''
    return ' at about {0:.1f}x'.format(
        (payload_bytes / float(elapsed)) / float(AUDIO_BYTES_PER_SECOND))


def _start_burn_log(header):
    """Truncate the per-burn timing log and write its header.

    Every line the burner produces is also written here with the time it
    arrived, so "the burner said nothing until the end" can be told apart from
    "we only showed it at the end".  Returns None if the file cannot be written.
    """
    path = os.path.join(acquire.CD_DIR, 'last_burn.log')
    try:
        os.makedirs(acquire.CD_DIR, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fp:
            fp.write('{0}\nstarted {1}\n'.format(
                header, time.strftime('%Y-%m-%d %H:%M:%S')))
    except OSError:
        return None
    return path


class _Screen:
    """The one line of terminal a burner is allowed to redraw.

    A progress bar is not a sequence of lines.  drutil (and every other CLI that
    draws one) rewrites the same line in place with carriage returns, backspaces
    and runs of spaces, which is why splitting the stream on \\r produced the
    unreadable fragments this class replaced.  Text is written into a single
    line buffer the way a terminal would, and only what is actually visible
    comes back out.
    """

    MAX_CHARS = 1000

    def __init__(self):
        self.chars = []
        self.col = 0
        self.escape = ''
        self.rewind = False

    def current(self):
        """What is on the line right now, minus trailing padding."""
        return ''.join(self.chars).rstrip()

    def erase(self, sequence):
        """Honour the erase sequences; ignore colours and cursor moves.

        A bar that redraws itself with shorter text leaves the tail of the
        previous one behind unless the line is cleared, and the tail is what
        turned the log into "...] 1%    2%": drutil clears with an escape, so
        the clearing has to be done here too or the reading of the line is a
        mixture of two of them.
        """
        body = sequence[2:] if sequence[:2] == '\x1b[' else ''
        if body in ('K', '0K', 'J', '0J'):
            del self.chars[self.col:]           # erase to the end of the line
        elif body in ('1K', '2K'):
            self.chars, self.col = [], 0        # erase the whole line

    def feed(self, text):
        """Write text in, returning the lines that are now complete."""
        finished = []
        for char in text:
            if self.escape:
                # inside an ANSI escape sequence: it ends on a letter
                self.escape += char
                if char.isalpha():
                    self.erase(self.escape)
                    self.escape = ''
                continue
            if char == '\x1b':
                self.escape = char
                continue
            if char == '\r':
                # hold off: this is either the first half of \r\n (one ordinary
                # line ending, which is what every Windows tool and this app's
                # own PowerShell script write) or a bar rewinding to redraw in
                # place.  Which one it is only becomes clear on the next
                # character, and clearing here would throw a whole line away.
                self.col = 0
                self.rewind = True
                continue
            if char == '\n':
                finished.append(self.current())
                self.chars, self.col = [], 0
                self.rewind = False
                continue
            if self.rewind:
                # a bare carriage return: the burner is about to redraw this
                # line, so say what was on it before it goes.  drutil announces
                # "Writing track 3 ..." on a line the bar then overwrites, and
                # that announcement is the useful part.
                if self.current():
                    finished.append(self.current())
                self.chars, self.col = [], 0
                self.rewind = False
            if char == '\b':
                self.col = max(0, self.col - 1)
                continue
            if char == '\t':
                char = ' '
            if char < ' ':
                continue
            if self.col < len(self.chars):
                self.chars[self.col] = char
            elif len(self.chars) < self.MAX_CHARS:
                self.chars.append(char)
            self.col += 1
        return finished


def _line_percent(text):
    """The disc figure on a burner line, or None.

    drutil draws one bar for the whole disc while it writes, and behind it the
    bars of the operations it runs around every track ("...] 100%  done.
    Closing... (this might take a while) [...] 2%").  A sub-operation finishing
    reports 100% of itself whatever the disc is doing, and those figures arrive
    on their own when a redraw is caught half-written - reading one as progress
    is what pinned the burner page's bar at the end of the first track for the
    rest of the burn.

    So: a line announcing a step is finished says nothing about the disc, and a
    figure of 100 is a bar that has just filled rather than the disc's own
    position (the Windows burner names the disc in its lines, so 100 is kept
    there).  What is left is the disc's figure, and when the line still carries
    the tail of the previous one the smaller of the two is the disc's: the
    figures sitting next to it belong to sub-operations on their way to 100%.
    """
    text = (text or '').lower()
    if 'done' in text:
        return None
    values = [int(m) for m in re.findall(r'(\d{1,3})\s*%', text)]
    values = [v for v in values if 0 <= v <= 100]
    if 100 in values and 'disc' not in text:
        values = [v for v in values if v != 100]
    return min(values) if values else None


def _progress_message(text, track=None, percent=None):
    """A readable label for the burner page's bar.

    Once the burner has said which track it is on, that is what the label says
    with the disc figure next to it: drutil's own lines are a bar redrawn in
    place, so what is left on them is a mixture of the last two drawings
    ("...] 100%  done.  Closing... [...] 2%") and no use as a label.  The
    Windows burner prints one tidy line per track, so its own words are used.
    """
    if track:
        label = 'Writing track {0}'.format(track)
        if percent is not None:
            return '{0} - {1}% of disc'.format(label, percent)
        return label
    return (text or '')[:120]


def _run_streaming(cmd, log=None, timeout=60 * 60, use_pty=False, heartbeat=20,
                   debug_path=None, progress=None):
    """Run the burner, handing its output on as it arrives.

    subprocess.run() holds every line back until the disc is finished, which is
    exactly why a burn looked like it had hung: no speed, no percentage, nothing
    until it was over.  Both burners are chatty (IMAPI2 prints a line per track)
    and drutil prints its progress bar only when it is talking to a terminal -
    hence the pseudo-terminal on the macOS path.

    debug_path, when given, receives every line with the time it arrived, which
    is how a burner that says nothing until it finishes can be told apart from
    one whose output is being held back.  progress, when given, is called with
    (percent, 100, line) whenever the percentage the burner is reporting changes.

    Returns (returncode, output, error); error is returned rather than raised so
    a failed burn can still report what the drive said.
    """
    output = []
    started = time.time()
    from_burner = {'lines': 0, 'first_at': None}

    def emit(text, source='app'):
        text = (text or '').strip()
        if not text:
            return
        if source == 'burner':
            # a redraw written over the previous one leaves long runs of spaces
            # behind - exactly what a terminal shows, and pure noise in a log
            text = re.sub(r' {3,}', ' ', text)
        output.append(text)
        if source == 'burner':
            from_burner['lines'] += 1
            if from_burner['first_at'] is None:
                from_burner['first_at'] = time.time() - started
        if log:
            log(text)
        if debug_path:
            try:
                with open(debug_path, 'a', encoding='utf-8') as fp:
                    fp.write('+{0:8.3f}  {1}{2}\n'.format(time.time() - started,
                                                          '' if source == 'burner' else '[app] ',
                                                          text))
            except OSError:
                pass

    # a pseudo-terminal makes an otherwise silent CLI behave as if a person were
    # watching, which is when drutil bothers to draw its progress bar.  The
    # window size matters: tools that draw a bar often print nothing at all when
    # the terminal they are given is 0 columns wide, which is what openpty()
    # hands out unless it is told otherwise.
    master = None
    slave = None
    mode = 'pipe'
    if use_pty and os.name != 'nt':
        try:
            import fcntl
            import struct
            import termios
            import pty
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ,
                        struct.pack('HHHH', 24, 80, 0, 0))
            mode = 'pseudo-terminal (80x24)'
        except Exception as e:
            master, slave = None, None
            mode = 'pipe (pty unavailable: {0})'.format(e)
    emit('burner output: {0}'.format(mode), 'app')

    try:
        if slave is not None:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=slave,
                                    stderr=slave, close_fds=True)
            os.close(slave)
        else:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
    except Exception as e:
        if master is not None:
            try:
                os.close(master)
            except OSError:
                pass
        return None, '', 'failed to start: {0}'.format(e)

    lines = queue.Queue()
    # both paths are read in chunks off a file descriptor rather than with
    # readline(): a burner that writes text with no line ending has to show up
    # when it is written, not when it finally gets a newline
    read_fd = master if master is not None else proc.stdout.fileno()

    def read_output():
        while True:
            try:
                chunk = os.read(read_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            lines.put(chunk.decode('utf-8', 'replace'))
        lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    def report_progress(text):
        """Feed the burner page's bar and its label from what the burner shows.

        The bar is the burner's own figure for the disc.  It is kept monotonic -
        a bar that redraws itself reports whatever it likes, and a figure that
        jumps backwards is a mis-read - and capped at 99 so the last step of the
        burn cannot fill the bar while the disc is still being closed.

        The label is sent whenever it changes rather than only when the figure
        does: the figure moves once every few seconds, and a label that waits for
        it sits on "Writing track 1" while the disc is on track 9.
        """
        nonlocal last_percent, last_message, last_sent, current_track

        if not progress or not text:
            return
        percent = _line_percent(text)
        if percent is not None:
            percent = min(99, max(last_percent or 0, percent))
        announced = _TRACK_LINE.search(text)
        if announced:
            current_track = int(announced.group(1))
        message = _progress_message(text, current_track,
                                   percent if percent is not None else last_percent)
        if not message:
            return

        now = time.time()
        moved = percent is not None and percent != last_percent
        if not moved and (message == last_message or now - last_sent < 1.0):
            return
        if percent is not None:
            last_percent = percent
        last_message = message
        last_sent = now
        progress(last_percent or 0, 100, message)

    screen = _Screen()
    shown = None
    last_percent = None
    last_message = ''
    last_sent = 0.0
    current_track = None
    last_output = time.time()
    last_note = started
    timed_out = False
    while True:
        try:
            chunk = lines.get(timeout=0.5)
        except queue.Empty:
            chunk = _NO_ITEM

        if chunk is None:                       # the reader saw the end of output
            break
        if chunk is not _NO_ITEM:
            last_output = time.time()
            for line in screen.feed(chunk):
                if line and line != shown:
                    shown = line
                    emit(line, 'burner')
                report_progress(line)
            report_progress(screen.current())
        elif screen.current() and time.time() - last_output >= 2.0:
            # a bar that is being redrawn in place never finishes a line, so
            # show what is on screen rather than staying silent until the burn
            # ends - which is exactly what "no live output" looked like
            visible = screen.current()
            if visible != shown:
                shown = visible
                emit(visible, 'burner')
            report_progress(visible)

        if proc.poll() is None and time.time() - started > timeout:
            timed_out = True
            proc.kill()
            break

        if (heartbeat and proc.poll() is None
                and time.time() - last_note >= heartbeat):
            last_note = time.time()
            emit('still burning - {0} elapsed, {1} line(s) from the burner so far'
                 .format(format_hms(time.time() - started), from_burner['lines']))

    returncode = proc.wait()
    reader.join(timeout=5)

    # whatever the reader queued between the last poll and the exit
    while True:
        try:
            chunk = lines.get_nowait()
        except queue.Empty:
            break
        if chunk:
            for line in screen.feed(chunk):
                if line and line != shown:
                    shown = line
                    emit(line, 'burner')

    if screen.current():
        emit(screen.current(), 'burner')

    if master is not None:
        try:
            os.close(master)
        except OSError:
            pass

    if from_burner['first_at'] is None:
        emit('the burner produced no output at all', 'app')
    else:
        emit('first burner output after {0:.1f}s, {1} line(s) in total'.format(
            from_burner['first_at'], from_burner['lines']), 'app')

    error = ('timed out after {0}'.format(format_hms(timeout)) if timed_out else '')
    return returncode, '\n'.join(output), error


def _burn_drutil(disc_folder, dry_run, verify, device, speed, log=None, progress=None,
                 test_burn=False, expected=None):
    """macOS: the built-in drutil burner (no install, no root).

    The disc is burned from a .toc layout that names every track in order, not
    from the folder: drutil's man page says a directory of audio is burned "in
    alphabetical order", but a directory is read in the file system's own order,
    which is how a disc came out with the page's track 8 as its track 1.  A .toc
    is burned as written, so the order is the plan's and nothing else's - and
    only the files it names can become tracks.
    """
    if shutil.which('drutil') is None:
        return _result(False, dry_run, 'drutil not found on this system.')

    names = list(expected or [])
    if not names:
        names = sorted(f for f in os.listdir(disc_folder)
                       if f.lower().endswith(WAV_EXTENSION))
    try:
        toc_path = write_audio_toc(disc_folder, names)
    except OSError as e:
        return _result(False, dry_run, 'The disc layout could not be written into '
                       'the disc folder: {0}'.format(e))

    def command(from_toc):
        cmd = ['drutil']
        if device:
            cmd += ['-drive', str(device)]
        cmd += ['burn']
        if test_burn:
            # drutil's own test burn: the drive goes through every step and the
            # progress output is exactly the same, but the disc is left blank
            cmd += ['-test']
        if not from_toc:
            # -audio means "the audio files in this directory are the tracks",
            # which is not what a layout file is: drutil reads a .toc as an image
            cmd += ['-audio']
        # -eject is left off: the disc is read back and ejected by hand below,
        # so the drive is still holding it afterwards
        cmd += ['-verify'] if verify else ['-noverify']
        if speed:
            cmd += ['-speed', str(speed)]
        cmd.append(toc_path if from_toc else disc_folder)
        return cmd

    def run(cmd):
        debug_path = _start_burn_log('drutil burn: ' + ' '.join(str(c) for c in cmd))
        began = time.time()
        rc, out, err = _run_streaming(cmd, log=log, timeout=60 * 60, use_pty=True,
                                      heartbeat=20, debug_path=debug_path,
                                      progress=progress)
        return rc, out, err, time.time() - began, debug_path

    cmd = command(True)
    audio_seconds, payload = _disc_audio(names, disc_folder)
    if log:
        log('{0} track(s), {1} of audio, {2:.0f} MB of PCM'.format(
            len(names), format_hms(audio_seconds), payload / 1048576.0))
        log('Disc layout written to {0} - drutil burns the files it names, in the '
            'order it names them.'.format(toc_path))
        if test_burn:
            log('Test burn: nothing will be written to the disc.')

    status = media_status()
    if dry_run:
        return _result(True, True, 'Dry run - nothing was written.', command=cmd,
                       media=status)
    if not status.get('present'):
        return _result(False, False,
                       'No blank media detected: {0}'.format(status.get('text')),
                       command=cmd, media=status)

    if log:
        log('Command: ' + ' '.join(str(c) for c in cmd))
        log('drutil reports its own progress when it sees a terminal, so that is '
            'what is shown below; a "still burning" line appears every 20s if it '
            'stays quiet.')

    returncode, output, error, elapsed, debug_path = run(cmd)

    if not error and returncode != 0 and not _announced_tracks(output):
        # nothing was written yet, so the layout itself was refused: drutil can
        # still have the folder.  The order it makes of a folder is the one that
        # has come out wrong before, so this is only done when the alternative is
        # no burn at all, and it is said out loud.
        if log:
            log('drutil would not take the disc layout (exit {0}); burning the '
                'folder instead.  From here the track order is whatever drutil '
                'makes of the folder, which is not necessarily the order above.'
                .format(returncode))
        cmd = command(False)
        if log:
            log('Command: ' + ' '.join(str(c) for c in cmd))
        returncode, output, error, elapsed, debug_path = run(cmd)

    if error:
        return _result(False, False, 'drutil could not be run: {0}'.format(error),
                       command=cmd)

    ok = returncode == 0
    missing = _missing_tracks(output, len(names)) if ok else []
    if log:
        if ok:
            log('Burn finished in {0}{1}.'.format(format_hms(elapsed),
                                                  _speed_note(payload, elapsed)))
            if missing:
                log('drutil announced {0} of the {1} planned track(s): the disc may '
                    'not hold track(s) {2}.'.format(
                        len(names) - len(missing), len(names),
                        ', '.join(str(n) for n in missing)))
        else:
            log('Burn failed after {0} (exit {1}).'.format(format_hms(elapsed), returncode))
        if debug_path:
            log('Every line with the time it arrived: {0}'.format(debug_path))

    # the disc is still in the drive: read back what it holds, then hand it over
    if test_burn:
        if log:
            log('Test burn: the disc was not written, so there is nothing to read '
                'back.')
    elif ok:
        _read_back_disc(device, log=log)
    _eject_disc(device, log=log)

    message = 'Burn finished.' if ok else 'Burn failed (exit {0}).'.format(returncode)
    if missing:
        message = ('Burn finished, but the burner announced only {0} of the {1} '
                   'track(s) - see the log.'.format(len(names) - len(missing),
                                                    len(names)))
    return _result(ok, False, message, command=cmd, streamed=True,
                   elapsed=round(elapsed, 1), output=output)


def _announced_tracks(output):
    """The track numbers the burner said it was writing."""
    return sorted({int(n) for n in _TRACK_LINE.findall(output or '')})


def _drutil_cmd(device, verb):
    return ['drutil'] + (['-drive', str(device)] if device else []) + [verb]


def _read_back_disc(device, log=None, timeout=30):
    """Log what the drive says is actually on the disc.

    A burn's command and its log say what was sent to the drive; this is the
    drive's own answer, which is the only way to see what the disc really holds -
    "Writing track N" only counts the tracks it started, and a disc that came out
    with two tracks missing looked exactly like a good burn in the log.  The
    report is printed as it arrives rather than picked apart, because the app has
    no business guessing what a version of drutil it has never seen means.

    Returns the text, or None if the drive would not say.
    """
    cmd = _drutil_cmd(device, 'trackinfo')
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        if log:
            log('The drive did not say what is on the disc ({0}).'.format(e))
        return None
    text = (out.stdout or '').strip()
    if not text:
        if log:
            log('The drive said nothing about what is on the disc.')
        return None
    if log:
        log('What the drive says is on the disc:')
        for line in text.splitlines():
            log('  ' + line)
    return text


def _eject_disc(device, log=None):
    """Push the disc out.

    The burn is no longer asked to eject (`-eject`): the disc has to stay in the
    drive long enough to be read back.  A drive that will not eject has not lost
    anything - the button still works - so it is reported with the command to try
    rather than treated as a failed burn.
    """
    cmd = _drutil_cmd(device, 'eject')
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        out = None
        why = str(e)
    if out is not None and out.returncode == 0:
        return True
    if log:
        why = ((out.stderr or out.stdout or '').strip() if out is not None else why)
        log('The disc was not ejected ({0}).  Press the drive\'s button, or run: {1}'
            .format(why or 'drutil said no', ' '.join(cmd)))
    return False


def _missing_tracks(output, planned):
    """Planned track numbers the burner never announced.

    drutil announces each track it starts writing ("Writing track 7 ..."), so a
    track it says nothing about is one it did not write - which is how a disc
    ends up with fewer tracks than the page lists and a burn log that looks fine.
    """
    announced = set(_announced_tracks(output))
    return [n for n in range(1, planned + 1) if n not in announced]


def _imapi2_devices():
    """Enumerate recorders through IMAPI2 (Windows only)."""
    if os.name != 'nt':
        return []
    script = (
        "try { $dm = New-Object -ComObject IMAPI2.MsftDiscMaster2; "
        "$i = 0; foreach ($id in $dm) { "
        "$r = New-Object -ComObject IMAPI2.MsftDiscRecorder2; "
        "$r.InitializeDiscRecorder($id); "
        "Write-Output ($i.ToString() + \"`t\" + $r.VendorId + \"`t\" + $r.ProductId); "
        "$i++ } } catch { }"
    )
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-NonInteractive',
                              '-Command', script],
                             capture_output=True, text=True, timeout=60)
    except Exception:
        return []
    devices = []
    for line in (out.stdout or '').splitlines():
        parts = line.strip().split('\t')
        if len(parts) >= 3:
            devices.append({
                'backend': 'imapi2',
                'id': parts[0],
                'name': '{0} {1}'.format(parts[1], parts[2]).strip(),
                'media': 'Unknown',
            })
    return devices


def _burn_imapi2(disc_folder, wavs, dry_run, verify, device, log=None, progress=None,
                 check_only=False):
    """Windows: the built-in IMAPI2 TrackAtOnce burner.

    The heavy lifting is done by a generated PowerShell script (see
    write_imapi2_script) because the COM interface needs a raw PCM stream and
    PowerShell can marshal that without a compiled helper.

    check_only runs the script's -Check mode: every track is parsed, its samples
    located and its sector padding worked out, which is everything a burn does
    apart from touching the drive.  Nothing is written and no disc is needed,
    which is the nearest thing Windows has to a test burn.
    """
    script_path = write_imapi2_script()
    cmd = ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
           '-File', script_path, '-DiscFolder', disc_folder]
    if device:
        cmd += ['-DriveIndex', str(device)]
    if not verify:
        cmd += ['-NoVerify']
    if check_only:
        cmd += ['-Check']
    elif dry_run:
        cmd += ['-DryRun']

    audio_seconds, payload = _disc_audio(wavs, disc_folder)
    if log:
        log('{0} track(s), {1} of audio, {2:.0f} MB of PCM'.format(
            len(wavs), format_hms(audio_seconds), payload / 1048576.0))

    if check_only:
        if log:
            log('Command: ' + ' '.join(str(c) for c in cmd))
        debug_path = _start_burn_log('imapi2 check: ' + ' '.join(str(c) for c in cmd))
        returncode, output, error = _run_streaming(cmd, log=log, timeout=10 * 60,
                                                   heartbeat=20, debug_path=debug_path)
        if error:
            return _result(False, True, 'PowerShell could not be run: {0}'.format(error),
                           command=cmd)
        ok = returncode == 0
        return _result(ok, True,
                       'The audio was checked - every track reads as raw samples and '
                       'is a whole number of CD sectors.  Nothing was written.'
                       if ok else 'The audio check failed - see the log.',
                       command=cmd, streamed=True, output=output)

    devices = _imapi2_devices()
    if dry_run:
        return _result(True, True,
                       'Dry run - nothing was written. '
                       'Detected {0} recorder(s).'.format(len(devices)),
                       command=cmd, devices=devices)
    if not devices:
        return _result(False, False, 'No optical drive found.', command=cmd)

    if log:
        log('Command: ' + ' '.join(str(c) for c in cmd))

    debug_path = _start_burn_log('imapi2 burn: ' + ' '.join(str(c) for c in cmd))
    started = time.time()
    returncode, output, error = _run_streaming(cmd, log=log, timeout=60 * 60,
                                               heartbeat=20, debug_path=debug_path,
                                               progress=progress)
    elapsed = time.time() - started

    if error:
        return _result(False, False, 'PowerShell could not be run: {0}'.format(error),
                       command=cmd)

    ok = returncode == 0
    if log:
        if ok:
            log('Burn finished in {0}{1}.'.format(format_hms(elapsed),
                                                  _speed_note(payload, elapsed)))
        else:
            log('Burn failed after {0} (exit {1}).'.format(format_hms(elapsed), returncode))
        if debug_path:
            log('Every line with the time it arrived: {0}'.format(debug_path))

    return _result(ok, False, 'Burn finished.' if ok else 'Burn failed (exit {0}).'
                   .format(returncode), command=cmd, streamed=True,
                   elapsed=round(elapsed, 1), output=output)


def write_imapi2_script():
    """Write the PowerShell helper that performs an IMAPI2 audio burn."""
    os.makedirs(os.path.dirname(IMAPI2_SCRIPT), exist_ok=True)
    with open(IMAPI2_SCRIPT, 'w', encoding='utf-8') as fp:
        fp.write(_IMAPI2_SCRIPT)
    return IMAPI2_SCRIPT


_IMAPI2_SCRIPT = r'''
<#
    Audio CD burning for DeckCastDJ using the built-in Windows IMAPI2 API.

    Writes a Red Book audio CD (Track At Once) from a folder of 44.1kHz
    16-bit stereo WAV files, in sorted file-name order.

    Two things this API is strict about, both learned the hard way:

      * AddAudioTrack takes raw audio samples with no container of any kind, so
        the WAV header has to be left out - and the audio does not start at a
        fixed offset, because ffmpeg writes a LIST chunk holding the track's
        tags between the format chunk and the audio.
      * the stream has to be a whole number of CD audio sectors (2352 bytes),
        or the track is refused with "the provided audio stream is not valid".

    Both are settled before the drive is touched, since a disc is spent the
    moment the media is prepared.
#>
param(
    [Parameter(Mandatory = $true)][string]$DiscFolder,
    [int]$DriveIndex = 0,
    # accepted so the app's command line is the same on every platform; IMAPI2
    # has no read-back verify, so there is nothing to turn off
    [switch]$NoVerify,
    [switch]$DryRun,
    # check the audio and nothing else: no drive, no disc, nothing written
    [switch]$Check,
    [string]$ClientName = 'DeckCastDJ'
)

$ErrorActionPreference = 'Stop'

# 44100 samples x 2 channels x 2 bytes per second, and the sector IMAPI2
# insists a track's stream be a whole number of
$CD_SECTOR = 2352
$CD_SECOND = 176400

# Progress lines go straight to the console and are flushed as they are written:
# the app streams this output to its terminal and log, and a buffered burn looks
# like a hung one.
function Write-Line([string]$text) {
    [Console]::Out.WriteLine($text)
    [Console]::Out.Flush()
}

# One line per finished track: how much of the disc is written, how fast the
# drive actually managed on that track, and where the burn is up to.  It is a
# function so it can be checked on its own - -f binds tighter than + in
# PowerShell, so "a" + "b" -f x would silently format only "b".
function Format-Progress {
    param(
        [int]$Track, [int]$Count, [int]$Percent, [string]$Name, [double]$Speed,
        [double]$Elapsed, [double]$Eta
    )
    $template = ("  [{0}/{1}] {2,3}% of disc - {3} - " +
        "{4:N1}x, elapsed {5}, eta {6}")
    $template -f $Track, $Count, $Percent, $Name, $Speed,
        [TimeSpan]::FromSeconds($Elapsed).ToString('hh\:mm\:ss'),
        [TimeSpan]::FromSeconds($Eta).ToString('hh\:mm\:ss')
}

function Format-Time([double]$Seconds) {
    $span = [TimeSpan]::FromSeconds([math]::Round($Seconds))
    '{0}:{1:d2}' -f [int][math]::Floor($span.TotalMinutes), $span.Seconds
}

# A stream of a track's raw samples, for the driver to read.  ADODB's stream is
# used because it is an IStream that PowerShell can hand to a COM method without
# a compiled helper in the way, and it is what other PowerShell burners do with
# this API.
function New-TrackStream {
    param([string]$Path)

    $stream = New-Object -ComObject ADODB.Stream
    $stream.Type = 1                    # binary
    $stream.Open()
    try {
        $stream.LoadFromFile($Path)
    } catch {
        $stream.Close()
        throw
    }
    return $stream
}

# Where the audio sits inside a WAV file, and how long it is.  The chunks are
# walked rather than assuming the usual 44 byte header: ffmpeg puts a LIST chunk
# holding the track's tags in front of the audio, which moves it.
function Get-WavAudio {
    param([System.IO.FileInfo]$File)

    $stream = [IO.File]::OpenRead($File.FullName)
    try {
        $head = New-Object byte[] 12
        if ($stream.Read($head, 0, 12) -ne 12) { throw "not a WAV file" }
        if ([Text.Encoding]::ASCII.GetString($head, 0, 4) -ne 'RIFF' -or
            [Text.Encoding]::ASCII.GetString($head, 8, 4) -ne 'WAVE') {
            throw "not a WAV file"
        }
        while ($true) {
            $chunk = New-Object byte[] 8
            if ($stream.Read($chunk, 0, 8) -ne 8) { throw "no audio data in it" }
            $id = [Text.Encoding]::ASCII.GetString($chunk, 0, 4)
            $size = [BitConverter]::ToInt32($chunk, 4)
            if ($id -eq 'data') {
                if ($size -le 0 -or $stream.Position + $size -gt $stream.Length) {
                    throw "its audio data is empty"
                }
                return [PSCustomObject]@{ Offset = $stream.Position; Bytes = $size }
            }
            # every chunk is padded to an even length
            $skip = $size + ($size % 2)
            if ($stream.Position + $skip -ge $stream.Length) { throw "no audio data in it" }
            $null = $stream.Seek($skip, [IO.SeekOrigin]::Current)
        }
    } finally {
        $stream.Dispose()
    }
}

# Copy a track's audio out as raw samples, padded with silence to a whole
# number of sectors, which is what AddAudioTrack will accept.
function Export-AlignedPcm {
    param([System.IO.FileInfo]$File, [long]$Offset, [long]$Bytes, [string]$Path)

    $source = [IO.File]::OpenRead($File.FullName)
    try {
        $null = $source.Seek($Offset, [IO.SeekOrigin]::Begin)
        $out = [IO.File]::Create($Path)
        try {
            $buffer = New-Object byte[] (1MB)
            $left = $Bytes
            while ($left -gt 0) {
                $read = $source.Read($buffer, 0, [int][math]::Min($buffer.Length, $left))
                if ($read -le 0) { throw "could not read the audio out of $($File.Name)" }
                $out.Write($buffer, 0, $read)
                $left -= $read
            }
            $pad = ($CD_SECTOR - ($Bytes % $CD_SECTOR)) % $CD_SECTOR
            if ($pad -gt 0) {
                $silence = New-Object byte[] $pad
                $out.Write($silence, 0, $pad)
            }
        } finally {
            $out.Dispose()
        }
    } finally {
        $source.Dispose()
    }
}

$wavs = @(Get-ChildItem -Path $DiscFolder -Filter *.wav | Sort-Object Name)
if ($wavs.Count -eq 0) { throw "No WAV files found in $DiscFolder" }
if ($wavs.Count -gt 99) { throw "An audio CD holds at most 99 tracks (found $($wavs.Count))" }

# Settle the audio before anything looks at the drive: what each track's samples
# are, and what they become once they are a whole number of sectors long.
$tracks = @()
$index = 0
foreach ($wav in $wavs) {
    $index++
    try {
        $audio = Get-WavAudio -File $wav
    } catch {
        throw "Track {0} ({1}) cannot be burned: {2}" -f $index, $wav.Name, $_.Exception.Message
    }
    $pad = ($CD_SECTOR - ($audio.Bytes % $CD_SECTOR)) % $CD_SECTOR
    $tracks += [PSCustomObject]@{
        Index   = $index
        File    = $wav
        Offset  = $audio.Offset
        Bytes   = $audio.Bytes
        Padded  = $audio.Bytes + $pad
        Silence = $pad
        Pcm     = '{0:d2}.pcm' -f $index
    }
}

$totalBytes = ($tracks | Measure-Object -Property Padded -Sum).Sum
$audioSeconds = $totalBytes / $CD_SECOND
$silences = @($tracks | Where-Object { $_.Silence -gt 0 })

Write-Line ("DeckCastDJ: $($tracks.Count) track(s), " +
    "$(Format-Time $audioSeconds) of audio, $([math]::Round($totalBytes / 1MB)) MB of PCM.")
if ($silences.Count -gt 0) {
    $paddedSeconds = ($silences | Measure-Object -Property Silence -Sum).Sum / $CD_SECOND
    Write-Line (('  {0} of {1} track(s) are not a whole number of CD sectors, so ' +
        '{2:N3}s of silence is added to them, which the format requires.') -f
        $silences.Count, $tracks.Count, $paddedSeconds)
}

if ($DryRun) {
    $tracks | ForEach-Object {
        Write-Line ("  dry-run: {0} - {1} of audio, {2} bytes of samples at {3}" -f
            $_.File.Name, (Format-Time ($_.Bytes / $CD_SECOND)), $_.Bytes, $_.Offset)
    }
    Write-Line 'Dry run complete - nothing was written.'
    exit 0
}

if ($Check) {
    $tracks | ForEach-Object {
        Write-Line ("  ok  {0,2}  {1} - {2} of audio, {3} bytes of samples at offset {4}" -f
            $_.Index, $_.File.Name, (Format-Time ($_.Bytes / $CD_SECOND)), $_.Bytes,
            $_.Offset)
    }
    Write-Line (('Check: {0} track(s), {1} of audio, {2} MB of PCM - every track ' +
        'reads as raw samples and is a whole number of CD sectors long. Nothing ' +
        'was written.') -f $tracks.Count, (Format-Time $audioSeconds),
        [math]::Round($totalBytes / 1MB))
    exit 0
}

# The audio is taken out of the WAVs and padded on the disk before the drive is
# prepared, in a folder beside the disc's, so that nothing is left that can fail
# once the disc has been committed.
$pcmDir = Join-Path (Split-Path (Resolve-Path -LiteralPath $DiscFolder) -Parent) '.pcm'

$free = (New-Object IO.DriveInfo ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($pcmDir)))).AvailableFreeSpace
if ($free -lt $totalBytes) {
    throw (('Not enough free space for the burn: the audio needs {0} MB and only ' +
        '{1} MB is free.') -f [math]::Round($totalBytes / 1MB), [math]::Round($free / 1MB))
}

if (Test-Path -LiteralPath $pcmDir) { Remove-Item -LiteralPath $pcmDir -Recurse -Force }
$null = New-Item -ItemType Directory -Path $pcmDir

try {
    foreach ($track in $tracks) {
        Export-AlignedPcm -File $track.File -Offset $track.Offset `
            -Bytes $track.Bytes -Path (Join-Path $pcmDir $track.Pcm)
    }
    Write-Line ("Audio prepared: $([math]::Round($totalBytes / 1MB)) MB of raw " +
        'samples beside the disc folder, nothing written to the disc yet.')
}
catch {
    Remove-Item -LiteralPath $pcmDir -Recurse -Force -ErrorAction SilentlyContinue
    throw
}

# The audio is handed to the driver as an ADODB stream, so make sure that is
# there before the media is prepared rather than after.
try {
    $null = New-Object -ComObject ADODB.Stream
} catch {
    throw ('The CD writer needs ADODB.Stream to hand it the audio, and it is not ' +
        'available on this system.')
}

$prepared = $false
$written = 0

try {
    # Locate the recorder.
    $discMaster = New-Object -ComObject IMAPI2.MsftDiscMaster2
    if ($discMaster.Count -eq 0) { throw 'No optical recorder found.' }

    $recorder = New-Object -ComObject IMAPI2.MsftDiscRecorder2
    $recorder.InitializeDiscRecorder($discMaster.Item($DriveIndex))
    Write-Line ("Recorder: " + $recorder.VendorId + ' ' + $recorder.ProductId)

    $format = New-Object -ComObject IMAPI2.MsftDiscFormat2TrackAtOnce
    $format.Recorder = $recorder
    $format.ClientName = $ClientName

    if (-not $format.IsCurrentMediaSupported($recorder)) {
        throw 'The inserted media is not supported by this recorder.'
    }
    if (-not $format.MediaPhysicallyBlank) {
        throw 'The inserted disc is not blank. Insert a blank CD-R and try again.'
    }

    $format.PrepareMedia()
    $prepared = $true
    Write-Line 'Media prepared; writing tracks...'

    $burnStart = Get-Date

    foreach ($track in $tracks) {
        $pcm = Join-Path $pcmDir $track.Pcm
        $stream = $null
        try {
            $stream = New-TrackStream -Path $pcm

            # track the wall time per track: bytes over seconds is the speed the
            # drive really managed, which is what a burn log is read for
            $trackStart = Get-Date
            $format.AddAudioTrack($stream)
            $trackSeconds = ((Get-Date) - $trackStart).TotalSeconds
        }
        finally {
            if ($null -ne $stream) {
                try { $stream.Close() } catch { }
                [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($stream)
            }
            Remove-Item -LiteralPath $pcm -Force -ErrorAction SilentlyContinue
        }

        $written += $track.Padded
        $percent = [int][math]::Round($written * 100 / $totalBytes)
        $elapsed = ((Get-Date) - $burnStart).TotalSeconds
        $trackSpeed = if ($trackSeconds -gt 0) {
            ($track.Padded / $trackSeconds) / $CD_SECOND
        } else { 0 }
        $eta = if ($percent -gt 0) {
            [math]::Round($elapsed * (100 - $percent) / $percent)
        } else { 0 }

        Write-Line (Format-Progress -Track $track.Index -Count $tracks.Count `
            -Percent $percent -Name $track.File.Name -Speed $trackSpeed `
            -Elapsed $elapsed -Eta $eta)
    }

    $format.ReleaseMedia()
    $burnSeconds = ((Get-Date) - $burnStart).TotalSeconds
    $average = if ($burnSeconds -gt 0) {
        ($totalBytes / $burnSeconds) / $CD_SECOND
    } else { 0 }
    Write-Line ("Burn complete in " +
        "$([TimeSpan]::FromSeconds($burnSeconds).ToString('hh\:mm\:ss')) " +
        "($('{0:N1}' -f $average)x average).")
    exit 0
}
catch {
    # closing the media is what finishes a disc off, so it is only done once a
    # track has actually been written: a failure before that leaves the disc
    # unused rather than half burnt
    if ($prepared -and $written -gt 0) {
        try { $format.ReleaseMedia() } catch { }
    }
    Write-Line ("Burn failed: " + $_.Exception.Message)
    exit 1
}
finally {
    Remove-Item -LiteralPath $pcmDir -Recurse -Force -ErrorAction SilentlyContinue
}
'''


# ---------------------------------------------------------------------------
# Cache cleanup
# ---------------------------------------------------------------------------


def cache_usage(project=None, tier='all'):
    """Return (bytes, file count) held by burner-owned media."""
    total = 0
    count = 0
    for path in _cache_files(project, tier=tier):
        try:
            total += os.path.getsize(path)
            count += 1
        except OSError:
            pass
    return total, count


def _cd_files(project=None, disc=None):
    """Every file under the CD cache, or just one project's or disc's folder."""
    files = []
    if project is not None and disc is not None:
        root = disc_dir(project, disc)
    elif project is not None:
        root = project_dir(project)
    else:
        root = acquire.CD_DIR
    if os.path.isdir(root):
        for dirpath, _dirnames, names in os.walk(root):
            files.extend(os.path.join(dirpath, n) for n in names)
    return files


def _all_source_files():
    """Every downloaded source, including the music library."""
    files = []
    for tree in (acquire.MUSIC_DIR, acquire.MUSIC_SRC_DIR):
        if os.path.isdir(tree):
            for root, _dirs, names in os.walk(tree):
                for name in names:
                    if name != acquire.AUDIODB_NAME:
                        files.append(os.path.join(root, name))
    if os.path.isdir(acquire.MEDIA_DIR):
        for name in os.listdir(acquire.MEDIA_DIR):
            if '_src.' in name:
                files.append(os.path.join(acquire.MEDIA_DIR, name))
    return files


def _cache_files(project, tier='wav'):
    """List burner-owned files, never including anything trackext.py owns.

    trackext's analysis cache is <videoId>_11k.wav; that name is explicitly
    excluded so clearing CD media can never force a re-download for the
    Track Numbers feature.  project=None sweeps the whole cache.
    """
    files = _cd_files(project)
    if tier == 'all':
        files.extend(_source_files_for(project) if project is not None
                     else _all_source_files())
    return files


def _source_files_for(project):
    """Source files used by one project, skipping any shared with another."""
    others = set()
    for key, other in PROJECTS.items():
        if key == project_key(project.get('username'), project.get('name')):
            continue
        for track in other.get('tracks', []):
            others.add((track.get('source'), track.get('ref')))

    files = []
    for track in project.get('tracks', []):
        ident = (track.get('source'), track.get('ref'))
        if ident in others:
            continue  # still needed by another project
        path = _find_source_on_disk(track)
        if path:
            files.append(path)
    return files


def clean(project=None, tier='wav', dry_run=True, log=None, all_projects=False,
          disc=None):
    """Delete burner media.  Returns (freed_bytes, deleted_paths, errors).

    all_projects=True sweeps the whole ytd/cd cache instead of just the open
    project's folder, which is what the burner page's Clear CD Cache button
    does: a renamed (or never named) working project would otherwise leave the
    media of every other project sitting on the disk.

    disc= restricts the sweep to one disc of that project, which is how the
    audio of a disc that has just been burned is cleared without touching the
    discs that are still waiting to be written.
    """
    files = _cd_files(None if all_projects else project,
                      None if all_projects else disc)
    if tier == 'all':
        # sources are only freed for a project we can inspect, so pressing the
        # button on the page can never wipe the music library out from under
        # the MP3 page
        files.extend(_source_files_for(project) if project is not None
                     else _all_source_files())
    freed = 0
    deleted = []
    errors = []

    for path in files:
        # belt and braces: never remove the Track Numbers analysis cache
        if re.search(r'_11k\.wav$', os.path.basename(path)):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if dry_run:
            freed += size
            deleted.append(path)
            continue
        try:
            os.remove(path)
            freed += size
            deleted.append(path)
        except OSError as e:
            errors.append('{0}: {1}'.format(path, e))

    if not dry_run:
        _prune_empty_dirs(log=log)
        if tier == 'all':
            _prune_empty_dirs(acquire.MUSIC_SRC_DIR, log=log)
    return freed, deleted, errors


def _prune_empty_dirs(root=None, log=None):
    """Remove the folders a clean has emptied, deepest first.

    Defaults to the CD cache.  Sweeps the whole tree rather than one project's
    folder, and retries: a folder emptied a moment ago can still be held by a
    sync client or a virus scanner, and a silent failure is what left empty
    ytd/cd/<project>/discN/ folders behind.

    A read-only folder is the other way this fails quietly: Windows refuses
    rmdir() on an otherwise empty folder while that bit is set (WinError 5),
    which is what a Google Drive / OneDrive root tends to leave behind.  The
    flag is cleared on the first failure, and anything still stuck is reported
    rather than swallowed.
    """
    root = root or acquire.CD_DIR
    if not os.path.isdir(root):
        return 0
    removed = 0
    failures = []
    root_abs = os.path.abspath(root)
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        if os.path.abspath(dirpath) == root_abs:
            continue
        error = None
        for attempt in range(3):
            try:
                if os.listdir(dirpath):
                    break  # hold a file and the folder is not ours to remove
                os.rmdir(dirpath)
                removed += 1
                error = None
                break
            except OSError as e:
                error = e
                if attempt == 0:
                    _clear_readonly(dirpath)
                time.sleep(0.15)
        if error is not None:
            failures.append('{0} ({1})'.format(dirpath, error))
    if log:
        if removed:
            log('Removed {0} empty folder(s).'.format(removed), 'ok')
        for failure in failures:
            log('Could not remove empty folder: {0}'.format(failure), 'err')
    return removed


def _clear_readonly(path):
    """Drop the read-only flag a sync client can leave on a folder."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_plan(args):
    tracks = []
    source = acquire.get_source(args.source)
    for ref in args.url:
        tracks.extend(source.resolve(ref, log=lambda message: print('  ' + message)))
    discs, warnings = plan(tracks, args.blank)
    print('{0} track(s), {1}'.format(len(tracks),
          format_hms(sum(t.get('seconds') or 0 for t in tracks))))
    for disc in discs:
        print('  Disc {0}: {1} / {2}  ({3} tracks, {4:.0f}% full)'.format(
            disc['index'], format_hms(disc['totalSeconds']),
            format_hms(disc['limitSeconds']), disc['trackCount'], disc['fill'] * 100))
    for track in tracks:
        print('    D{0} #{1:02d}  {2:>7}  {3}'.format(
            track['discIndex'], track['trackNo'],
            format_hms(track['seconds']), track['title']))
    for warning in warnings:
        print('  WARNING: ' + warning)
    return 0


def _cmd_acquire(args):
    project = _require_project(args.project)
    ok, errors = acquire_project(project, log=lambda m: print('  ' + str(m)))
    save_projects()
    for err in errors:
        print('  ERROR: ' + err)
    return 0 if ok else 1


def _cmd_premaster(args):
    project = _require_project(args.project)
    discs = [int(args.disc)] if args.disc else [d['index'] for d in project.get('discs', [])]
    rc = 0
    for index in discs:
        ok, errors = premaster_disc(
            project, index,
            progress=lambda i, n, t, text=None: print('  disc{0} [{1}/{2}] {3}'.format(
                index, i, n, (t or {}).get('title', '')) if t else ''),
            log=lambda m: print('  ' + str(m)))
        for err in errors:
            print('  ERROR: ' + err)
            rc = 1
    save_projects()
    return rc


def _cmd_burn(args):
    project = _require_project(args.project)
    discs = [int(args.disc)] if args.disc else [d['index'] for d in project.get('discs', [])]
    rc = 0
    for index in discs:
        result = burn_disc(project, index, dry_run=args.dry_run,
                           verify=not args.no_verify, device=args.device,
                           speed=args.speed, test_burn=args.test,
                           check_only=args.check,
                           log=lambda line: print('  ' + line, flush=True),
                           progress=lambda percent, total, message: print(
                               '  progress {0}%'.format(percent), flush=True))
        print('Disc {0}: {1}'.format(index, result['message']))
        if result.get('command'):
            print('  command: ' + ' '.join(str(c) for c in result['command']))
        # the burner's own output was already streamed above when it ran
        if not result.get('streamed'):
            for key in ('stdout', 'stderr'):
                if result.get(key):
                    print(result[key])
        if not result['success']:
            rc = 1
        elif not result['dryRun'] and not getattr(args, 'test', False):
            for disc in project.get('discs', []):
                if int(disc.get('index', 0)) == index:
                    disc['burnedAt'] = time.strftime('%Y-%m-%dT%H:%M:%S')
            project['status'] = 'burned'
    save_projects()
    return rc


# A scripted stand-in for a burner, so the progress display can be checked on a
# machine with no drive and no disc.  Every shape in it is taken from a real
# drutil burn: the track announcements, one bar for the whole disc redrawn in
# place with carriage returns, a redraw caught half written (which leaves the
# tail of the last one on the line, and so two figures on it), the step that
# ends a track reporting 100% of itself, and the closing work after it drawing
# a bar of its own.  It takes the track count so the runner knows where the disc
# is supposed to be at every moment and can check the bar against it.
_DEMO_BURNER = r'''
import sys, time

def write(text):
    sys.stdout.write(text)
    sys.stdout.flush()

count = int(sys.argv[1]) if len(sys.argv) > 1 else 4
truth = open(sys.argv[2], 'w') if len(sys.argv) > 2 else None
tracks = ['{0:02d} - demo track.wav'.format(n) for n in range(1, count + 1)]

def bar(percent):
    filled = int(round(percent / 2.0))
    return '[' + '*' * filled + ' ' * (50 - filled) + '] ' + str(percent) + '%'

def disc(percent):
    """Redraw the disc's bar, and note where the disc really is for the check."""
    if truth:
        truth.write('{0:.3f} {1}\n'.format(time.time(), percent))
        truth.flush()
    write('\r' + bar(percent) + ' ')

write('Burning Audio Disc: ytd/cd/demo/disc1\n')
for name in tracks:
    write('2026-01-01 00:00:00.000 drutil[58186:12507760] Found: ' + name + '\n')
time.sleep(0.3)
write('Preparing... done.')
time.sleep(0.2)

step = 0
for index in range(1, count + 1):
    write('          Writing track {0} ...'.format(index))
    time.sleep(0.2)
    target = int(round(index * 100.0 / count))
    while step < target:
        step += 1
        disc(step)
        time.sleep(0.02)
        if step % 4 == 0:
            write('\r' + bar(step - 1) + '   ' + str(step) + '%')
            time.sleep(0.02)
    # the step that ends the track fills a bar and says so; a moment later that
    # bar is the only thing on the line, and it is 100% of that step, not of the
    # disc - reading it as the disc's position is what stuck the bar on track 1
    write('\r' + bar(100))
    time.sleep(0.05)
    write('  done.  Closing... (this might take a while)')
    time.sleep(0.05)
    write('\r' + ' ' * 90 + '\r' + bar(step) + '   Closing... (this might take a while)')
    time.sleep(0.05)
disc(100)
write('  done.\n')
write('Burn complete.\n')
if truth:
    truth.close()
'''


def _cmd_progress_demo(args):
    """Replay a burner's progress through the real capture path.

    Checks as well as shows.  The demo burner keeps its own record of where the
    disc really is, so the figure the page's bar is being driven with can be
    held against it: a bar that runs ahead of the burn, or that sticks part way
    through, fails here rather than on a disc.
    """
    script = os.path.join(acquire.CD_DIR, 'demo_burner.py')
    truth_path = os.path.join(acquire.CD_DIR, 'demo_burner_truth.txt')
    try:
        os.makedirs(acquire.CD_DIR, exist_ok=True)
        with open(script, 'w', encoding='utf-8') as fp:
            fp.write(_DEMO_BURNER)
    except OSError as e:
        print('Could not write the demo script: {0}'.format(e))
        return 1

    tracks = 4
    print('This is a scripted stand-in for drutil: no drive, no disc, nothing')
    print('written.  It goes through the same capture path a real burn uses, so')
    print('what the burner page and the terminal show here is what a burn shows.')
    print('-' * 70)

    started = time.time()
    seen = []

    def report(line):
        print('  +{0:6.1f}s  {1}'.format(time.time() - started, line), flush=True)

    def track(percent, total, message):
        seen.append((time.time(), percent))
        if percent == 1 or percent % 10 == 0 or percent >= 95:
            print('      page bar [{0}{1}] {2:3}%  {3}'.format(
                '#' * (percent // 2), ' ' * (50 - percent // 2), percent,
                message), flush=True)

    rc, _output, error = _run_streaming([sys.executable, '-u', script, str(tracks),
                                         truth_path],
                                        log=report, use_pty=args.pty, heartbeat=20,
                                        progress=track)
    print('-' * 70)
    print('exit {0}{1}'.format(rc, ' - ' + error if error else ''))

    truth = []
    try:
        with open(truth_path, encoding='utf-8') as fp:
            for line in fp:
                when, where = line.split()
                truth.append((float(when), int(where)))
    except (OSError, ValueError):
        pass

    def where_it_really_was(when):
        """The disc position the demo had reached at that moment."""
        latest = 0
        for stamp, position in truth:
            if stamp <= when:
                latest = position
            else:
                break
        return latest

    ahead = max([percent - where_it_really_was(when)
                 for when, percent in seen] + [0])
    behind = max([where_it_really_was(when) - percent
                  for when, percent in seen if where_it_really_was(when)] + [0])
    last = seen[-1][1] if seen else 0
    real_end = truth[-1][1] if truth else 0

    checks = [
        ('the bar never ran ahead of the disc', ahead <= 1,
         'worst {0}% ahead'.format(ahead)),
        ('the bar kept up with the disc', behind <= 5,
         'worst {0}% behind'.format(behind)),
        ('the bar moved all the way through the burn', len(set(p for _t, p in seen)) >= 50,
         '{0} figure(s) reported'.format(len(set(p for _t, p in seen)))),
        ('the bar reached the end of the disc', real_end >= 95 and last >= real_end - 5,
         'last figure {0}%, disc at {1}%'.format(last, real_end)),
    ]
    for name, ok, detail in checks:
        print('{0:<46} {1}  ({2})'.format(name, 'ok' if ok else 'FAILED', detail))
    passed = all(ok for _n, ok, _d in checks)
    print('{0}: the bar below the log is the one the burner page draws, so a burn '
          'should track it the same way.'.format('PASS' if passed else 'FAIL'))
    for path in (script, truth_path):
        try:
            os.remove(path)
        except OSError:
            pass
    return 0 if passed and not error else 1


def _cmd_clean(args):
    load_projects()
    project = None
    if args.project:
        project = get_project(args.project) or _require_project(args.project)
    freed, paths, errors = clean(project, tier=args.tier, dry_run=args.dry_run)
    print('{0} {1} file(s), {2:.1f} MB'.format(
        'Would free' if args.dry_run else 'Freed', len(paths), freed / 1048576.0))
    for path in paths:
        print('  ' + path)
    for err in errors:
        print('  ERROR: ' + err)
    return 0 if not errors else 1


def _require_project(key):
    if not PROJECTS:
        load_projects()
    project = get_project(key)
    if project is None:
        available = ', '.join(sorted(PROJECTS.keys())) or '(none)'
        print('No such project: {0}'.format(key))
        print('Known projects: {0}'.format(available))
        sys.exit(2)
    return project


def main(argv=None):
    parser = argparse.ArgumentParser(description='DeckCastDJ CD burner')
    sub = parser.add_subparsers(dest='command')

    p_plan = sub.add_parser('plan', help='resolve URLs and show the disc layout')
    p_plan.add_argument('--url', action='append', required=True,
                        help='video/playlist URL or video id (repeatable)')
    p_plan.add_argument('--source', default='youtube',
                        choices=[acquire.YouTubeSource.name, acquire.MusicHostSource.name])
    p_plan.add_argument('--blank', type=int, default=DEFAULT_BLANK,
                        help='blank size in minutes (74 or 80)')
    p_plan.set_defaults(func=_cmd_plan)

    p_acq = sub.add_parser('acquire', help='download sources for a project')
    p_acq.add_argument('--project', required=True, help='project key (user@name)')
    p_acq.set_defaults(func=_cmd_acquire)

    p_pre = sub.add_parser('premaster', help='convert a project to CD-ready WAV')
    p_pre.add_argument('--project', required=True)
    p_pre.add_argument('--disc', type=int, help='only this disc (default: all)')
    p_pre.set_defaults(func=_cmd_premaster)

    p_burn = sub.add_parser('burn', help='burn a project (nothing is written with '
                            '--dry-run, --test or --check)')
    p_burn.add_argument('--project', required=True)
    p_burn.add_argument('--disc', type=int, help='only this disc (default: all)')
    p_burn.add_argument('--dry-run', action='store_true', default=False)
    p_burn.add_argument('--test', action='store_true', default=False,
                        help='simulated burn: drutil goes through every step but '
                             'the disc is left blank (macOS only; Windows checks '
                             'the audio instead)')
    p_burn.add_argument('--check', action='store_true', default=False,
                        help='check and stop, with no drive and no disc: that the '
                             'folder matches the plan and every track holds 44.1kHz '
                             '16-bit stereo PCM (Windows also works out the sector '
                             'padding)')
    p_burn.add_argument('--no-verify', action='store_true')
    p_burn.add_argument('--device', help='drive index/name to use')
    p_burn.add_argument('--speed', type=int)
    p_burn.set_defaults(func=_cmd_burn)

    p_demo = sub.add_parser('progress-demo',
                            help='show what a burn prints, with no drive and no disc')
    p_demo.add_argument('--pty', action='store_true', default=True,
                        help='capture through a pseudo-terminal (default)')
    p_demo.add_argument('--no-pty', dest='pty', action='store_false',
                        help='capture through a pipe instead')
    p_demo.set_defaults(func=_cmd_progress_demo)

    p_clean = sub.add_parser('clean', help='delete burner media to reclaim space')
    p_clean.add_argument('--project', help='only this project (default: everything)')
    p_clean.add_argument('--tier', default='wav', choices=['wav', 'all'])
    p_clean.add_argument('--dry-run', action='store_true', default=False)
    p_clean.set_defaults(func=_cmd_clean)

    args = parser.parse_args(argv)
    if not getattr(args, 'command', None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print('Interrupted.')
        return 130
    except Exception as e:
        # keep the CLI friendly: report the cause without a raw traceback
        print('Error: {0}'.format(e))
        return 1


if __name__ == '__main__':
    sys.exit(main())
