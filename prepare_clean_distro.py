#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DeckCastDJ - Clean Distribution Data Generator
Creates de-personalized, generic distribution data files.
Strips personal history, bookmarks, mix tracks, cache, and API keys.
"""

import os
import sys
import json
import shutil
import argparse

def get_base_dir():
    return os.path.dirname(os.path.abspath(__file__))

def load_clean_config(config_path):
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"Clean playlist configuration not found at: {config_path}")
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_api_key():
    """Attempt to retrieve an API key to fetch YouTube metadata during clean build."""
    try:
        from config import youtubeApiKey
        if youtubeApiKey and youtubeApiKey.strip():
            return youtubeApiKey.strip()
    except Exception:
        pass
    settings_path = os.path.join(get_base_dir(), 'data', 'settings.json')
    if os.path.isfile(settings_path):
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                s = json.load(f)
                if 'youtubeApiKey' in s and s['youtubeApiKey'].strip():
                    return s['youtubeApiKey'].strip()
        except Exception:
            pass
    return ""

def fetch_playlist_items(url, username, pafy_mod):
    """Fetch playlist items using pafy."""
    print(f"  Fetching YouTube playlist for '{username}': {url}")
    yt_list = pafy_mod.get_playlist2(url)
    items = {}
    total = yt_list._len
    count = 0
    for v in yt_list:
        count += 1
        items[v.videoid] = [v.title, v.thumb, v.duration, v.published, username]
        if count % 25 == 0 or count == total:
            print(f"    Loaded {count}/{total} tracks...")
    return items

def build_clean_dataset(clean_cfg, output_dir):
    """Fetch and construct pre-populated clean dataset."""
    os.makedirs(output_dir, exist_ok=True)

    import pafy
    api_key = get_api_key()
    if api_key:
        pafy.set_api_key(api_key)

    # 1. Default Guest Playlist (playlist.json)
    guest_cfg = clean_cfg.get("default_playlist", {})
    guest_url = guest_cfg.get("url", "")
    guest_items = {}
    if guest_url:
        guest_items = fetch_playlist_items(guest_url, "Guest", pafy)
    
    playlist_json_path = os.path.join(output_dir, "playlist.json")
    with open(playlist_json_path, 'w', encoding='utf-8') as f:
        json.dump(guest_items, f, indent=2)
    print(f"  [OK] Created clean playlist.json ({len(guest_items)} tracks)")

    # 2. User Playlists (userPlaylist.json)
    user_playlists_cfg = clean_cfg.get("user_playlists", {})
    user_playlist_data = {}
    merged_playlist = {}

    for cat_name, cat_url in user_playlists_cfg.items():
        cat_key = cat_name.lower()
        items = fetch_playlist_items(cat_url, cat_key, pafy)
        user_playlist_data[cat_key] = items
        for vid, data in items.items():
            if vid not in merged_playlist:
                merged_playlist[vid] = data

    # Add the merged playlist category
    merged_key = f"all merged ({len(merged_playlist)})"
    user_playlist_data[merged_key] = merged_playlist

    user_playlist_json_path = os.path.join(output_dir, "userPlaylist.json")
    with open(user_playlist_json_path, 'w', encoding='utf-8') as f:
        json.dump(user_playlist_data, f, indent=2)
    print(f"  [OK] Created clean userPlaylist.json ({len(user_playlists_cfg)} categories, {len(merged_playlist)} unique tracks)")

    # 3. Write Empty Skeleton Files (de-personalized)
    skeletons = {
        "bookmarks.json": {},
        "mixTrack.json": {},
        "tracklists.json": {},
        "tracknums.json": {},
        "queList.json": {},
        "invalidVideos.json": []
    }

    for filename, empty_content in skeletons.items():
        filepath = os.path.join(output_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(empty_content, f, indent=2)
        print(f"  [OK] Created empty skeleton: {filename}")

    # 4. Ensure personal/cache files are NOT present
    for excluded in ["pafyCache.pkl", "settings.json", "test_playlist.txt"]:
        ex_path = os.path.join(output_dir, excluded)
        if os.path.exists(ex_path):
            os.remove(ex_path)

def generate_clean_config_py(dest_path, clean_cfg):
    """Generate a clean, de-personalized config.py for distribution."""
    user_pl = clean_cfg.get("user_playlists", {})
    pl_lines = []
    for name, url in user_pl.items():
        pl_lines.append(f'youtubePL["{name}"] = "{url}"')
    pl_block = "\n".join(pl_lines)

    content = f'''# -*- coding: utf-8 -*-
"""
DeckCastDJ - Generic Distribution Configuration
"""

# what port the server should run on
app_port = 5054

# YouTube Data API v3 key (enter your key here or via the web UI settings)
youtubeApiKey = ''

# used for loading playlist from youtube. 
# If false, a backup is loaded from disk of previously loaded playlist
useYoutube = False

# change/add urls to your own youtube playlist of interest
youtubePL = dict()

{pl_block}
'''
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"  [OK] Created clean config.py at {dest_path}")

def is_cache_valid(cache_dir):
    """Check if the cache directory contains required clean files."""
    if not os.path.isdir(cache_dir):
        return False
    required = ["playlist.json", "userPlaylist.json", "bookmarks.json"]
    for req in required:
        p = os.path.join(cache_dir, req)
        if not os.path.isfile(p) or os.path.getsize(p) == 0:
            return False
    return True

def copy_clean_dir(src_dir, dest_dir):
    """Copy all files from clean cache into destination directory."""
    os.makedirs(dest_dir, exist_ok=True)
    for fn in os.listdir(src_dir):
        src_file = os.path.join(src_dir, fn)
        dest_file = os.path.join(dest_dir, fn)
        if os.path.isfile(src_file):
            shutil.copy2(src_file, dest_file)

def main():
    parser = argparse.ArgumentParser(description="Prepare de-personalized clean distribution data for DeckCastDJ.")
    parser.add_argument("--dest", default=None, help="Destination directory for clean data files.")
    parser.add_argument("--config-dest", default=None, help="Destination file path for clean config.py.")
    parser.add_argument("--cache-dir", default=None, help="Directory to cache clean pre-populated data.")
    parser.add_argument("--clean-config", default=None, help="Path to clean_playlists.json.")
    parser.add_argument("--refresh", action="store_true", help="Force re-fetching from YouTube even if cache exists.")
    args = parser.parse_args()

    base_dir = get_base_dir()
    clean_cfg_path = args.clean_config or os.path.join(base_dir, "clean_playlists.json")
    cache_dir = args.cache_dir or os.path.join(base_dir, "data_clean")
    dest_dir = args.dest

    print("========================================================")
    print("DeckCastDJ - Clean Distribution Generator")
    print("========================================================")

    clean_cfg = load_clean_config(clean_cfg_path)

    # Check cache status
    if not args.refresh and is_cache_valid(cache_dir):
        print(f"Using existing clean cache from: {cache_dir}")
    else:
        print(f"Building clean dataset into cache: {cache_dir}")
        build_clean_dataset(clean_cfg, cache_dir)

    # Copy to destination if specified
    if dest_dir:
        abs_dest = os.path.abspath(dest_dir)
        abs_cache = os.path.abspath(cache_dir)
        if abs_dest != abs_cache:
            print(f"Deploying clean data to: {dest_dir}")
            copy_clean_dir(cache_dir, dest_dir)

    # Generate clean config.py if specified
    if args.config_dest:
        generate_clean_config_py(args.config_dest, clean_cfg)

    print("========================================================")
    print("[SUCCESS] Clean distribution data ready!")
    print("========================================================")

if __name__ == "__main__":
    main()
