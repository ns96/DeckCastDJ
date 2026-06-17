import os
import sys
import json
import argparse
import time
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from trackext import extract_tracks

# =====================================================================
# GLOBAL CONFIGURATION DEFAULTS (Edit these to adjust default behavior)
# =====================================================================
TEST_MODE = False       # If True, extracts tracks but does not write to tracknums.json
DELETE_WAV = False     # If True, deletes downloaded/converted WAV files after processing
FORCE_PROCESS = True   # If True, forces reprocessing of already extracted videos
THREADS = 16           # Default number of concurrent threads
MIN_LEN = "8:00"      # Default minimum duration threshold (MM:SS or HH:MM:SS)
MAX_VIDEOS = 0         # Max number of videos to process (0 = no limit)

# Tuning Parameters
METHOD = "adaptive"          # 'adaptive' or 'fixed'
MIN_DISTANCE = 45            # Minimum separation between transition peaks in seconds
PROMINENCE = 0.035           # Static peak prominence threshold (only for 'fixed' method)
ADAPTIVE_WINDOW = 150        # Window size in seconds for rolling median filter
THRESHOLD_OFFSET = 0.04      # Height offset added to rolling median threshold
# =====================================================================

def parse_duration(dur_str):
    if not isinstance(dur_str, str):
        return 0
    try:
        parts = dur_str.strip().split(':')
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 1:
            return int(parts[0])
    except ValueError:
        pass
    return 0

def write_to_log(success_count, failure_count, elapsed):
    log_path = "batchext.log"
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    duration_str = str(datetime.timedelta(seconds=round(elapsed)))
    log_line = f"[{timestamp}] Videos processed: {success_count} succeeded, {failure_count} failed. Time taken: {duration_str} ({elapsed:.2f}s).\n"
    try:
        with open(log_path, 'a', encoding='utf-8') as lf:
            lf.write(log_line)
        print(f"Logged run status to {log_path}")
    except Exception as e:
        print(f"Warning: Could not write to {log_path}: {e}")

def process_video(video_id, title, dur_str, delete_wav, save_to_json, method, min_distance, prominence, adaptive_window, offset):
    print(f"[START] Processing video {video_id} (Length: {dur_str}): {title}")
    try:
        track_list = extract_tracks(
            video_id, 
            delete=delete_wav, 
            save=save_to_json,
            method=method,
            min_distance=min_distance,
            prominence=prominence,
            adaptive_window=adaptive_window,
            offset=offset
        )
        num_tracks = len(track_list)
        print(f"[SUCCESS] Finished video {video_id} (Length: {dur_str}) - Extracted {num_tracks} tracks")
        return video_id, True, num_tracks, None
    except KeyboardInterrupt:
        raise
    except BaseException as e:
        print(f"[ERROR] Failed video {video_id} (Length: {dur_str}): {e}")
        return video_id, False, 0, str(e)

def main():
    start_time = time.time()
    parser = argparse.ArgumentParser(description="Batch extract tracks from playlists using trackext.")
    parser.add_argument("--threads", "-t", type=int, default=THREADS, help=f"Number of concurrent threads (default: {THREADS})")
    parser.add_argument("--min-len", "-m", default=MIN_LEN, help=f"Minimum duration threshold (default: {MIN_LEN})")
    parser.add_argument("--limit", "-l", type=int, default=MAX_VIDEOS, help=f"Max number of videos to process (0 = no limit, default: {MAX_VIDEOS})")
    parser.add_argument("--force", "-f", action="store_true", help="Force reprocessing of already extracted videos")
    
    # Tuning Parameters CLI options (override defaults)
    parser.add_argument("--method", default=METHOD, choices=["adaptive", "fixed"], help=f"Peak detection method (default: {METHOD})")
    parser.add_argument("--min-distance", type=int, default=MIN_DISTANCE, help=f"Minimum separation (seconds) between transition peaks (default: {MIN_DISTANCE})")
    parser.add_argument("--prominence", type=float, default=PROMINENCE, help=f"Static peak prominence threshold (default: {PROMINENCE})")
    parser.add_argument("--adaptive-window", type=int, default=ADAPTIVE_WINDOW, help=f"Window size (seconds) for rolling median filter (default: {ADAPTIVE_WINDOW})")
    parser.add_argument("--offset", type=float, default=THRESHOLD_OFFSET, help=f"Height offset added to rolling median threshold (default: {THRESHOLD_OFFSET})")
    
    # CLI flags to override configuration defaults
    parser.add_argument("--no-test", action="store_true", help="Disable test mode and save extractions to tracknums.json")
    parser.add_argument("--delete-wav", action="store_true", help="Delete downloaded/converted WAV files after extraction")
    args = parser.parse_args()

    # Determine runtime options (CLI flags take precedence over defaults)
    test_mode_active = TEST_MODE and (not args.no_test)
    delete_wav_active = DELETE_WAV or args.delete_wav
    force_active = FORCE_PROCESS or args.force
    save_to_json = not test_mode_active
    limit_val = args.limit

    print("=====================================================================")
    print(f"RUN MODE: {'TEST MODE (NO SAVING)' if test_mode_active else 'WRITE MODE (SAVES TO JSON)'}")
    print(f"CLEANUP: {'DELETE WAVS' if delete_wav_active else 'KEEP/CACHE WAVS'}")
    print(f"FORCE REPROCESS: {'YES' if force_active else 'NO'}")
    print(f"LIMIT QUEUE: {limit_val if limit_val > 0 else 'NO LIMIT'}")
    print(f"METHOD: {args.method}")
    print(f"MIN DISTANCE: {args.min_distance}s")
    if args.method == "fixed":
        print(f"PROMINENCE: {args.prominence}")
    else:
        print(f"ADAPTIVE WINDOW: {args.adaptive_window}s")
        print(f"THRESHOLD OFFSET: {args.offset}")
    print("=====================================================================")

    # Aggregate and deduplicate all videos
    all_videos = {}

    # 1. Read playlist.json
    playlist_path = os.path.join("data", "playlist.json")
    if os.path.exists(playlist_path):
        with open(playlist_path, 'r', encoding='utf-8') as f:
            try:
                playlist_data = json.load(f)
                for vid, info in playlist_data.items():
                    all_videos[vid] = info
            except Exception as e:
                print(f"Warning: Could not parse playlist.json: {e}")

    # 2. Read userPlaylist.json
    user_playlist_path = os.path.join("data", "userPlaylist.json")
    if os.path.exists(user_playlist_path):
        with open(user_playlist_path, 'r', encoding='utf-8') as f:
            try:
                user_playlist_data = json.load(f)
                for pl_name, pl_dict in user_playlist_data.items():
                    if isinstance(pl_dict, dict):
                        for vid, info in pl_dict.items():
                            all_videos[vid] = info
            except Exception as e:
                print(f"Warning: Could not parse userPlaylist.json: {e}")

    if not all_videos:
        print("Error: No video records found in playlist files.")
        elapsed = time.time() - start_time
        write_to_log(0, 0, elapsed)
        return

    # Read existing extractions
    tracknums_path = os.path.join("data", "tracknums.json")
    existing_ids = set()
    if os.path.exists(tracknums_path) and not force_active and save_to_json:
        try:
            with open(tracknums_path, 'r', encoding='utf-8') as f:
                existing_ids = set(json.load(f).keys())
        except Exception:
            pass

    min_seconds = parse_duration(args.min_len)
    
    # Filter videos
    queue = []
    for video_id, info in all_videos.items():
        title = info[0]
        dur_str = info[2]
        if parse_duration(dur_str) >= min_seconds:
            if video_id not in existing_ids:
                queue.append((video_id, title, dur_str))

    # Apply queue limits
    if limit_val > 0:
        queue = queue[:limit_val]

    if not queue:
        print("No videos match criteria or all are already processed.")
        elapsed = time.time() - start_time
        write_to_log(0, 0, elapsed)
        return

    print(f"Aggregated {len(all_videos)} unique videos from database files.")
    print(f"Found {len(queue)} videos to process (duration >= {args.min_len}).")
    print(f"Starting ThreadPoolExecutor with {args.threads} threads...")

    success_count = 0
    failure_count = 0
    
    try:
        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            futures = {
                executor.submit(
                    process_video, 
                    vid, 
                    title, 
                    dur_str, 
                    delete_wav_active, 
                    save_to_json,
                    args.method,
                    args.min_distance,
                    args.prominence,
                    args.adaptive_window,
                    args.offset
                ): vid 
                for vid, title, dur_str in queue
            }
            for future in as_completed(futures):
                vid = futures[future]
                _, success, num_tracks, err = future.result()
                if success:
                    success_count += 1
                else:
                    failure_count += 1
    except KeyboardInterrupt:
        print("\n[INTERRUPT] Keyboard interrupt received. Exiting immediately...")
        elapsed = time.time() - start_time
        write_to_log(success_count, failure_count, elapsed)
        # Force terminate the process and all child threads/subprocesses instantly
        os._exit(1)

    print("\nBatch extraction complete.")
    print(f"Successful: {success_count} | Failed: {failure_count}")
    
    elapsed = time.time() - start_time
    write_to_log(success_count, failure_count, elapsed)

if __name__ == "__main__":
    main()
