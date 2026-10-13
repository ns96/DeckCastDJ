#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DeckCastDJ - Server Release Archive Builder

Packs everything the app needs to run into a single zip that can be copied to
the server and unzipped over the existing installation.

The archive deliberately carries no per-machine state:

  data/      playlists, track numbers, bookmarks, saved CD projects
  ytd/       downloaded and premastered media
  config.py  holds the YouTube API key

Those must survive an update, so the server keeps its own copies and the zip
only replaces code.  Anything git ignores but the app still loads at run time -
the generated QR images under static/ - *is* included, which is why the file
list is explicit rather than "everything git tracks".

cookies.txt is the one piece of per-machine state that *is* packed.  It is not
code, but it is what stops YouTube refusing the server's downloads, and
re-exporting it by hand after every update is needless work - so it travels
whenever it exists.  That makes the archive a credential file: it is only
meant for the owner's own server, never for passing around.

Usage:
    python tools/make_release.py                 # dist/DeckCastDJ-<ver>-<date>.zip
    python tools/make_release.py --no-tools      # skip the standalone utilities
    python tools/make_release.py --dry-run       # list files, write nothing
"""

import argparse
import datetime
import os
import re
import sys
import zipfile

# This script lives in tools/, so the project root is its parent.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- what ships ------------------------------------------------------------
# Explicit, so a new folder in the project never lands in a release by accident.
ROOT_FILES = ('main.py', 'README.md')
RUNTIME_DIRS = ('src', 'templates', 'static')
TOOLS_DIR = 'tools'

# Optional, but packed whenever present: the only YouTube credential source.
COOKIE_FILE = 'cookies.txt'

# --- what never ships, even inside a shipped directory ---------------------
EXCLUDED_NAMES = ('config.py', '.DS_Store', 'Thumbs.db')
EXCLUDED_DIRS = ('__pycache__', '.git', '.vscode', 'data', 'ytd', 'data_clean',
                 'dist', 'load_test', 'venv', '.venv')
EXCLUDED_SUFFIXES = ('.pyc', '.pyo', '.log')
EXCLUDED_PATTERNS = (re.compile(r'_old\.html$'),)

# --- reported as left behind, so the omission is never a surprise ----------
NOT_SHIPPED = (
    'data/  (playlists, track numbers, saved CD projects)',
    'ytd/  (downloaded sources and premastered WAVs)',
    'data_clean/, dist/, load_test/',
    'config.py  (per-machine: holds the YouTube API key)',
    'deno.exe  (JS runtime - installed per machine)',
    '.flaskenv, .gitignore, pyrightconfig.json, .vscode/',
    'build.bat, build.sh, DeckCastDJ.spec, listmp3.sh, clean_playlists.json',
)

CONFIG_IMPORT = re.compile(r'from\s+config\s+import\s+([^\n#]+)')


def cookie_jar_is_anonymous(path):
    """True when a Netscape-format jar holds no signed-in YouTube session."""
    signed_in = {'SID', 'HSID', 'SSID', 'LOGIN_INFO'}
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
        return False
    return bool(names) and not (names & signed_in)


def _is_excluded(name):
    if name in EXCLUDED_NAMES:
        return True
    if name.endswith(EXCLUDED_SUFFIXES):
        return True
    return any(pattern.search(name) for pattern in EXCLUDED_PATTERNS)


def read_version():
    """Pull the app version out of main.py so the archive name matches the UI."""
    path = os.path.join(ROOT_DIR, 'main.py')
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            match = re.match(r"\s*appVersion\s*=\s*['\"]([^'\"]+)['\"]", line)
            if match:
                version = match.group(1)
                number = re.search(r'\d+(?:\.\d+)+', version)
                return number.group(0) if number else version.replace(' ', '-')
    raise SystemExit('[ERROR] Could not find appVersion in main.py')


def collect(with_tools=True):
    """Return [(absolute_path, archive_name)] for everything that ships."""
    wanted = [(os.path.join(ROOT_DIR, name), name) for name in ROOT_FILES]
    for missing in [rel for full, rel in wanted if not os.path.isfile(full)]:
        raise SystemExit('[ERROR] Required file is missing: {0}'.format(missing))

    # Credentials, not code - but the server needs them for yt-dlp, so they ride
    # along when they exist.  Optional: the app runs without them (anonymously).
    cookies = os.path.join(ROOT_DIR, COOKIE_FILE)
    if os.path.isfile(cookies):
        wanted.append((cookies, COOKIE_FILE))

    for rel_dir in list(RUNTIME_DIRS) + ([TOOLS_DIR] if with_tools else []):
        abs_dir = os.path.join(ROOT_DIR, rel_dir)
        if not os.path.isdir(abs_dir):
            raise SystemExit('[ERROR] Required folder is missing: {0}'.format(rel_dir))
        for dirpath, dirnames, filenames in os.walk(abs_dir):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
            for name in sorted(filenames):
                if _is_excluded(name):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, ROOT_DIR).replace(os.sep, '/')
                wanted.append((full, rel))

    seen, entries = set(), []
    for full, rel in wanted:
        if rel in seen:
            raise SystemExit('[ERROR] Duplicate archive entry: {0}'.format(rel))
        seen.add(rel)
        entries.append((full, rel))
    return entries


def config_requirements():
    """Names the code imports from config.py, plus the ones it does not define.

    A release never replaces config.py, so if a new setting is added in code the
    server's copy needs it by hand - this reports what that copy must provide.
    """
    names = []
    for path in [os.path.join(ROOT_DIR, 'main.py')] + _python_files():
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                match = CONFIG_IMPORT.search(line)
                if not match:
                    continue
                for name in match.group(1).split(','):
                    name = name.strip().strip('()')
                    if name.isidentifier() and name not in names:
                        names.append(name)
    return names


def _python_files():
    paths = []
    for rel_dir in RUNTIME_DIRS + (TOOLS_DIR,):
        abs_dir = os.path.join(ROOT_DIR, rel_dir)
        if not os.path.isdir(abs_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(abs_dir):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
            paths.extend(os.path.join(dirpath, n) for n in sorted(filenames)
                         if n.endswith('.py'))
    return paths


def local_config_names():
    path = os.path.join(ROOT_DIR, 'config.py')
    defined = set()
    if not os.path.isfile(path):
        return defined
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            match = re.match(r'([A-Za-z_]\w*)\s*=', line)
            if match:
                defined.add(match.group(1))
    return defined


def summarize(entries):
    groups = {}
    for full, rel in entries:
        top = rel.split('/')[0] if '/' in rel else rel
        count, size = groups.get(top, (0, 0))
        groups[top] = (count + 1, size + os.path.getsize(full))
    return groups


def build(entries, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, name)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for full, rel in entries:
            archive.write(full, rel)
    return target


def main():
    parser = argparse.ArgumentParser(
        description='Build the code-only DeckCastDJ release archive.')
    parser.add_argument('--out', default='dist',
                        help='output folder, relative to the project root (default: dist)')
    parser.add_argument('--no-tools', dest='with_tools', action='store_false',
                        help='leave out tools/ (the standalone utilities)')
    parser.add_argument('--dry-run', action='store_true',
                        help='list what would be packed without writing a zip')
    args = parser.parse_args()

    version = read_version()
    stamp = datetime.date.today().isoformat()
    name = 'DeckCastDJ-{0}-{1}.zip'.format(version, stamp)

    entries = collect(args.with_tools)
    groups = summarize(entries)
    total = sum(size for _, size in groups.values())

    print('DeckCastDJ release {0}  ->  {1}'.format(version, name))
    print('')
    print('Included ({0} files, {1:.2f} MB):'.format(len(entries), total / 1024.0 / 1024.0))
    for top in sorted(groups):
        count, size = groups[top]
        print('  {0:<14} {1:>4} file(s)  {2:>8.2f} MB'.format(top, count, size / 1024.0 / 1024.0))

    print('')
    print('Left on the server (not in the archive):')
    for item in NOT_SHIPPED:
        print('  ' + item)

    print('')
    ships_cookies = any(rel == COOKIE_FILE for _, rel in entries)
    if ships_cookies:
        print('[NOTE] {0} is in the archive: it holds a signed-in YouTube session,'.format(COOKIE_FILE))
        print('       so this zip is itself a credential file - keep it off shared')
        print('       storage and do not pass it to anyone else.')
        if cookie_jar_is_anonymous(os.path.join(ROOT_DIR, COOKIE_FILE)):
            print('[WARNING] ...but that file has no sign-in cookies (SID/HSID/SSID/')
            print('          LOGIN_INFO), so it came from a signed-out browser. The')
            print('          server would still be told to sign in - re-export it with')
            print("          'Get cookies.txt LOCALLY' while signed in, then rebuild.")
    else:
        print('[WARNING] {0} was not found, so the archive carries no YouTube'.format(COOKIE_FILE))
        print('          credentials. Downloads there will run anonymously and may')
        print('          be refused with "Sign in to confirm you\'re not a bot" until')
        print('          a fresh export is copied over.')

    print('')
    print('config.py is not replaced. The server copy must define:')
    required = config_requirements()
    defined = local_config_names()
    for item in required:
        print('  {0}{1}'.format(item, '' if item in defined else '   <-- MISSING locally'))

    if args.dry_run:
        print('')
        print('--dry-run: no archive written. Files that would be packed:')
        for _, rel in entries:
            print('  ' + rel)
        return 0

    target = build(entries, os.path.join(ROOT_DIR, args.out), name)
    print('')
    print('Wrote {0} ({1:.2f} MB)'.format(
        os.path.relpath(target, ROOT_DIR).replace(os.sep, '/'),
        os.path.getsize(target) / 1024.0 / 1024.0))
    print('')
    print('Copy it over and unzip on top of the existing install:')
    print('  scp {0} user@host:~/'.format(name))
    print('  ssh user@host')
    print('  unzip -o ~/{0} -d ~/DeckCastDJ'.format(name))
    print('  systemctl restart deckcast          # or: kill + nohup python3.11 main.py &')
    print('Keep the previous zip - re-copying it is the rollback.')

    missing = [n for n in required if n not in defined]
    if missing:
        print('')
        print('[WARNING] local config.py does not define: {0}'.format(', '.join(missing)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
