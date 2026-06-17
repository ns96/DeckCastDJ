import os
import sys
import json
import argparse
import time
import datetime
import numpy as np
import scipy.signal
from scipy.io import wavfile
import scipy.ndimage
from concurrent.futures import ProcessPoolExecutor, as_completed
from trackext import extract_tracks, get_audio_file, compute_novelty_curve

# =====================================================================
# GLOBAL CONFIGURATION DEFAULTS
# =====================================================================
#DEFAULT_VIDEO = "nkxlU6Df7Oo"   # Video ID to tune, or "all" to run on all curated videos
DEFAULT_VIDEO = "all"   # Video ID to tune, or "all" to run on all curated videos
TEST_MODE = False               # If True, finds best params but does not save final extraction to tracknums.json
BLIND_MODE = True              # If True, runs blind mode by default (Grid Consensus)
THREADS = 16                    # Number of concurrent threads for multi-video tuning
MIN_LEN = "8:00"                # Default minimum duration threshold (MM:SS or HH:MM:SS)
# =====================================================================

# Grid parameter settings
METHOD = "adaptive"
MIN_DISTANCE_GRID = [20, 25, 45, 75]
ADAPTIVE_WINDOW_GRID = [45, 55, 75, 100, 150, 200, 250, 300]
THRESHOLD_OFFSET_RANGE = (0.010, 0.060, 0.001)  # (min, max, increment)

# Generate grid list dynamically
_min, _max, _inc = THRESHOLD_OFFSET_RANGE
THRESHOLD_OFFSET_GRID = []
_val = _min
while _val <= _max + 1e-9:
    THRESHOLD_OFFSET_GRID.append(round(_val, 6))
    _val += _inc


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


def compute_video_novelty_once(wav_path):
    """
    Loads downsampled WAV file, calculates transition novelty via STFT,
    and returns (novelty, times, sr, duration).
    """
    sr, y = wavfile.read(wav_path)
    
    # Convert to mono if it's stereo
    if len(y.shape) > 1:
        y = np.mean(y, axis=1)
        
    # Convert to float32 normalized representation
    if y.dtype == np.int16:
        y = y.astype(np.float32) / 32768.0
    elif y.dtype == np.int32:
        y = y.astype(np.float32) / 2147483648.0
    elif y.dtype == np.uint8:
        y = (y.astype(np.float32) - 128.0) / 128.0
        
    duration = len(y) / sr
    
    n_fft = 2048
    hop_length = 512
    
    frequencies, times, Zxx = scipy.signal.stft(
        y, 
        fs=sr, 
        nperseg=n_fft, 
        noverlap=n_fft - hop_length
    )
    magnitude = np.abs(Zxx)
    features = np.log1p(magnitude * 100)
    
    window_sec = 10
    W = int(round(window_sec * sr / hop_length))
    
    novelty = compute_novelty_curve(features, W)
    return novelty, times, sr, duration

def evaluate_grid_combination(novelty, times, sr, duration, min_distance_sec, adaptive_window_sec, offset):
    """
    Runs fast peak detection on precomputed novelty curve.
    """
    hop_length = 512
    min_dist_frames = int(round(min_distance_sec * sr / hop_length))
    
    kernel_size = int(round(adaptive_window_sec * sr / hop_length))
    if kernel_size % 2 == 0:
        kernel_size += 1
        
    local_median = scipy.ndimage.median_filter(novelty, size=kernel_size)
    threshold_curve = local_median + offset
    
    peaks, _ = scipy.signal.find_peaks(
        novelty, 
        distance=min_dist_frames, 
        height=threshold_curve
    )
    
    timestamps = times[peaks]
    valid_transitions = [t for t in timestamps if t > 10 and t < duration - 20]
    return valid_transitions

def tune_single_video(video_id, target_count, test_mode):
    """
    Tunes parameters for a single video ID using grid search on cached novelty curve.
    If target_count is None, runs blind tuning using Grid Consensus (Centroid of Stability).
    """
    start_time = time.time()
    num_combinations = len(MIN_DISTANCE_GRID) * len(ADAPTIVE_WINDOW_GRID) * len(THRESHOLD_OFFSET_GRID)
    if target_count is not None:
        print(f"[START] Tuning video {video_id} (Target count: {target_count} tracks) - Running {num_combinations} parameter combinations...")
    else:
        print(f"[START] Blind tuning video {video_id} (Grid Consensus) - Running {num_combinations} parameter combinations...")
        
    try:
        # Download and cache WAV
        wav_path = get_audio_file(video_id)
        
        # Compute novelty curve once
        novelty, times, sr, duration = compute_video_novelty_once(wav_path)
        
        # Grid search loop
        trials = []
        counts = {}
        for min_distance in MIN_DISTANCE_GRID:
            for adaptive_window in ADAPTIVE_WINDOW_GRID:
                for offset in THRESHOLD_OFFSET_GRID:
                    transitions = evaluate_grid_combination(
                        novelty, times, sr, duration,
                        min_distance, adaptive_window, offset
                    )
                    extracted_count = len(transitions) + 1
                    trial_data = {
                        "min_distance": min_distance,
                        "adaptive_window": adaptive_window,
                        "offset": offset,
                        "extracted_count": extracted_count
                    }
                    if target_count is not None:
                        trial_data["diff"] = abs(extracted_count - target_count)
                    trials.append(trial_data)
                    counts[extracted_count] = counts.get(extracted_count, 0) + 1
        
        if target_count is not None:
            # Supervised mode: Sort trials by difference (ascending)
            trials.sort(key=lambda x: x["diff"])
            best_trial = trials[0]
        else:
            # Blind mode: Find modal count (most frequent track count)
            sorted_counts = sorted(counts.items(), key=lambda x: (-x[1], -x[0]))
            modal_count = sorted_counts[0][0]
            
            modal_trials = [t for t in trials if t["extracted_count"] == modal_count]
            
            # Normalize coordinates to [0, 1] range to compute the centroid
            min_dist_min, min_dist_max = min(MIN_DISTANCE_GRID), max(MIN_DISTANCE_GRID)
            min_dist_range = min_dist_max - min_dist_min if min_dist_max > min_dist_min else 1.0

            window_min, window_max = min(ADAPTIVE_WINDOW_GRID), max(ADAPTIVE_WINDOW_GRID)
            window_range = window_max - window_min if window_max > window_min else 1.0

            offset_min, offset_max = min(THRESHOLD_OFFSET_GRID), max(THRESHOLD_OFFSET_GRID)
            offset_range = offset_max - offset_min if offset_max > offset_min else 1.0
            
            modal_coords = []
            for t in modal_trials:
                x = (t["min_distance"] - min_dist_min) / min_dist_range
                y = (t["adaptive_window"] - window_min) / window_range
                z = (t["offset"] - offset_min) / offset_range
                modal_coords.append((x, y, z))
                
            xs = [c[0] for c in modal_coords]
            ys = [c[1] for c in modal_coords]
            zs = [c[2] for c in modal_coords]
            
            centroid_x = sum(xs) / len(xs)
            centroid_y = sum(ys) / len(ys)
            centroid_z = sum(zs) / len(zs)
            
            best_trial = None
            min_distance_to_centroid = float('inf')
            for idx, t in enumerate(modal_trials):
                x, y, z = modal_coords[idx]
                dist = np.sqrt((x - centroid_x)**2 + (y - centroid_y)**2 + (z - centroid_z)**2)
                if dist < min_distance_to_centroid:
                    min_distance_to_centroid = dist
                    best_trial = t
            
            # Dummy diff for compatibility in reporting
            best_trial["diff"] = 0
            
        elapsed = time.time() - start_time
        print(f"[SUCCESS] Tuned video {video_id} in {elapsed:.2f}s.")
        print(f"          Best Params: min_dist={best_trial['min_distance']}s, window={best_trial['adaptive_window']}s, offset={best_trial['offset']}")
        if target_count is not None:
            print(f"          Track Count: Curated {target_count} vs Extracted {best_trial['extracted_count']} (Diff: {best_trial['diff']})")
        else:
            print(f"          Track Count: Selected Modal Count {best_trial['extracted_count']} (Consensus size: {counts[modal_count]} trials)")
        
        # Final optimal extraction to write database if not in test mode
        if not test_mode:
            print(f"          Generating final optimal extraction for {video_id}...")
            track_list = extract_tracks(
                video_id,
                delete=False,
                method=METHOD,
                min_distance=best_trial['min_distance'],
                adaptive_window=best_trial['adaptive_window'],
                offset=best_trial['offset'],
                save=False
            )
            best_trial["track_list"] = track_list
            
        return video_id, True, best_trial, trials, elapsed, None
    except KeyboardInterrupt:
        raise
    except BaseException as e:
        elapsed = time.time() - start_time
        print(f"[ERROR] Failed tuning video {video_id} after {elapsed:.2f}s: {e}")
        return video_id, False, None, [], elapsed, str(e)

def main():
    start_run_time = time.time()
    parser = argparse.ArgumentParser(description="Tune track extraction parameters against curated tracklists.")
    parser.add_argument("--video", default=DEFAULT_VIDEO, help=f"Video ID to tune (default: {DEFAULT_VIDEO}). Use 'all' to run on all curated videos.")
    parser.add_argument("--threads", "-t", type=int, default=THREADS, help=f"Number of concurrent threads (default: {THREADS})")
    parser.add_argument("--test-mode", action="store_true", help="Force test mode (do not save final extraction results to tracknums.json)")
    parser.add_argument("--no-test", action="store_true", help="Disable test mode and save final optimal extraction to tracknums.json")
    parser.add_argument("--blind", action="store_true", help="Force blind mode (process videos not in tracklists.json using Grid Consensus)")
    parser.add_argument("--no-blind", action="store_true", help="Disable blind mode (process videos in tracklists.json)")
    parser.add_argument("--min-len", "-m", default=MIN_LEN, help=f"Minimum duration threshold (default: {MIN_LEN})")
    args = parser.parse_args()

    # Determine test mode active
    test_mode_active = TEST_MODE
    if args.test_mode:
        test_mode_active = True
    if args.no_test:
        test_mode_active = False

    # Determine blind mode active
    blind_active = BLIND_MODE
    if args.blind:
        blind_active = True
    if args.no_blind:
        blind_active = False

    # Load curated tracklists
    tracklists_path = os.path.join("data", "tracklists.json")
    tracklists = {}
    if os.path.exists(tracklists_path):
        with open(tracklists_path, "r", encoding="utf-8") as f:
            try:
                tracklists = json.load(f)
            except Exception as e:
                print(f"Error: Could not parse {tracklists_path}: {e}")
                sys.exit(1)
    else:
        if not blind_active:
            print(f"Error: {tracklists_path} not found, required for supervised mode.")
            sys.exit(1)

    # Load all playlist videos to get duration metadata
    all_playlist_videos = {}

    # 1. Read playlist.json
    playlist_path = os.path.join("data", "playlist.json")
    if os.path.exists(playlist_path):
        with open(playlist_path, 'r', encoding='utf-8') as f:
            try:
                playlist_data = json.load(f)
                for vid, info in playlist_data.items():
                    all_playlist_videos[vid] = info
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
                            all_playlist_videos[vid] = info
            except Exception as e:
                print(f"Warning: Could not parse userPlaylist.json: {e}")

    # Determine candidates pool: combine playlist videos and curated tracklists
    candidates = dict(all_playlist_videos)
    for vid in tracklists:
        if vid not in candidates:
            candidates[vid] = (f"Curated Video {vid}", "Unknown", "00:00:00")

    if blind_active:
        # Hybrid strategy: process all videos in candidates (both playlist and curated)
        if args.video.lower() == "all":
            videos_to_tune = list(candidates.keys())
        else:
            if args.video not in candidates:
                print(f"Error: Video ID {args.video} not found in playlist files or curated tracklists.")
                sys.exit(1)
            videos_to_tune = [args.video]
    else:
        # Supervised mode: only process videos that have curated tracklists
        if args.video.lower() == "all":
            videos_to_tune = list(tracklists.keys())
        else:
            if args.video not in tracklists:
                print(f"Error: Video ID {args.video} not found in tracklists.json.")
                sys.exit(1)
            videos_to_tune = [args.video]

    # Filter by minimum duration threshold (MIN_LEN / --min-len)
    min_seconds = parse_duration(args.min_len)
    if min_seconds > 0:
        filtered_videos = []
        for vid in videos_to_tune:
            # Bypass duration filter for explicitly requested single videos
            if args.video.lower() != "all":
                filtered_videos.append(vid)
                continue

            if vid in candidates:
                dur_str = candidates[vid][2]
                if dur_str == "00:00:00" or parse_duration(dur_str) >= min_seconds:
                    filtered_videos.append(vid)
            else:
                filtered_videos.append(vid)
        videos_to_tune = filtered_videos

    print("=====================================================================")
    print(f"Tuning script: tunext.py")
    print(f"Mode: {'TEST MODE (NO SAVING)' if test_mode_active else 'WRITE MODE (SAVES TO tracknums.json)'}")
    print(f"Tuning Strategy: {'HYBRID (BLIND & SUPERVISED)' if blind_active else 'SUPERVISED (CURATED TARGETS)'}")
    print(f"Minimum Duration: {args.min_len}")
    print(f"Videos to process: {len(videos_to_tune)}")
    print(f"Threads: {args.threads if len(videos_to_tune) > 1 else 1}")
    print("=====================================================================")

    results = []
    success_count = 0
    failure_count = 0
    
    # Run tuning (concurrency if multi-video)
    if len(videos_to_tune) == 1:
        vid = videos_to_tune[0]
        target_count = len(tracklists[vid]) if vid in tracklists else None
        vid, success, best_trial, trials, elapsed, err = tune_single_video(vid, target_count, test_mode_active)
        if success:
            results.append((vid, best_trial, trials, elapsed, target_count))
            success_count += 1
        else:
            failure_count += 1
    else:
        try:
            with ProcessPoolExecutor(max_workers=args.threads) as executor:
                futures = {
                    executor.submit(
                        tune_single_video,
                        vid,
                        len(tracklists[vid]) if vid in tracklists else None,
                        test_mode_active
                    ): vid
                    for vid in videos_to_tune
                }
                for future in as_completed(futures):
                    vid, success, best_trial, trials, elapsed, err = future.result()
                    if success:
                        t_count = len(tracklists[vid]) if vid in tracklists else None
                        results.append((vid, best_trial, trials, elapsed, t_count))
                        success_count += 1
                    else:
                        failure_count += 1
        except KeyboardInterrupt:
            print("\n[INTERRUPT] Keyboard interrupt received. Exiting immediately...")
            os._exit(1)

    # Aggregating global errors if multiple videos processed
    global_rankings = []
    if len(results) > 1:
        # Accumulate differences for each parameter set
        global_errors = {}
        for vid, best_trial, trials, _, target_count in results:
            if target_count is not None:
                # Supervised: use count diff
                for trial in trials:
                    key = (trial["min_distance"], trial["adaptive_window"], trial["offset"])
                    if key not in global_errors:
                        global_errors[key] = []
                    global_errors[key].append(trial["diff"])
            else:
                # Unsupervised: calculate normalized distance to this video's modal centroid
                min_dist_min, min_dist_max = min(MIN_DISTANCE_GRID), max(MIN_DISTANCE_GRID)
                min_dist_range = min_dist_max - min_dist_min if min_dist_max > min_dist_min else 1.0

                window_min, window_max = min(ADAPTIVE_WINDOW_GRID), max(ADAPTIVE_WINDOW_GRID)
                window_range = window_max - window_min if window_max > window_min else 1.0

                offset_min, offset_max = min(THRESHOLD_OFFSET_GRID), max(THRESHOLD_OFFSET_GRID)
                offset_range = offset_max - offset_min if offset_max > offset_min else 1.0
                
                # Compute centroid for this video's modal count
                counts = {}
                for trial in trials:
                    c = trial["extracted_count"]
                    counts[c] = counts.get(c, 0) + 1
                sorted_counts = sorted(counts.items(), key=lambda x: (-x[1], -x[0]))
                modal_count = sorted_counts[0][0]
                
                modal_trials = [t for t in trials if t["extracted_count"] == modal_count]
                modal_coords = []
                for t in modal_trials:
                    x = (t["min_distance"] - min_dist_min) / min_dist_range
                    y = (t["adaptive_window"] - window_min) / window_range
                    z = (t["offset"] - offset_min) / offset_range
                    modal_coords.append((x, y, z))
                
                xs = [c[0] for c in modal_coords]
                ys = [c[1] for c in modal_coords]
                zs = [c[2] for c in modal_coords]
                
                centroid_x = sum(xs) / len(xs)
                centroid_y = sum(ys) / len(ys)
                centroid_z = sum(zs) / len(zs)
                
                # Calculate distance for all trials in this video
                for trial in trials:
                    key = (trial["min_distance"], trial["adaptive_window"], trial["offset"])
                    tx = (trial["min_distance"] - min_dist_min) / min_dist_range
                    ty = (trial["adaptive_window"] - window_min) / window_range
                    tz = (trial["offset"] - offset_min) / offset_range
                    dist = np.sqrt((tx - centroid_x)**2 + (ty - centroid_y)**2 + (tz - centroid_z)**2)
                    
                    if key not in global_errors:
                        global_errors[key] = []
                    global_errors[key].append(dist)
                
        # Compute ranking scores
        for key, scores in global_errors.items():
            avg_score = sum(scores) / len(scores)
            global_rankings.append({
                "params": key,
                "avg_score": avg_score,
                "max_score": max(scores)
            })
            
        global_rankings.sort(key=lambda x: x["avg_score"])
        
        print("\n" + "="*60)
        print("GLOBAL PARAMETER OPTIMIZATION ANALYSIS")
        print("="*60)
        if blind_active:
            has_curated = any(r[4] is not None for r in results)
            if has_curated:
                print("Top 3 Optimal Global Parameter Settings (Hybrid Tuning - Combined Diff & Centroid):")
                for rank, rank_data in enumerate(global_rankings[:3], 1):
                    params = rank_data["params"]
                    print(f"  {rank}. Avg Score: {rank_data['avg_score']:.4f} (Max: {rank_data['max_score']:.4f})")
                    print(f"     Params: min_dist={params[0]}s, window={params[1]}s, offset={params[2]}")
            else:
                print("Top 3 Optimal Global Parameter Settings (Grid Consensus Centroid):")
                for rank, rank_data in enumerate(global_rankings[:3], 1):
                    params = rank_data["params"]
                    print(f"  {rank}. Avg Distance to Centroid: {rank_data['avg_score']:.4f} (Max: {rank_data['max_score']:.4f})")
                    print(f"     Params: min_dist={params[0]}s, window={params[1]}s, offset={params[2]}")
        else:
            print("Top 3 Optimal Global Parameter Settings (Adaptive Method):")
            for rank, rank_data in enumerate(global_rankings[:3], 1):
                params = rank_data["params"]
                print(f"  {rank}. Avg Diff: {rank_data['avg_score']:.2f} tracks (Max Diff: {rank_data['max_score']})")
                print(f"     Params: min_dist={params[0]}s, window={params[1]}s, offset={params[2]}")
        print("="*60)

    # Write final optimal extractions sequentially to tracknums.json
    if not test_mode_active and success_count > 0:
        print("\nSaving final optimal extractions to tracknums.json...")
        json_path = os.path.join("data", "tracknums.json")
        tracknums_data = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    tracknums_data = json.load(f)
            except Exception:
                pass
        for vid, best_trial, _, _, _ in results:
            if best_trial and "track_list" in best_trial:
                tracknums_data[vid] = best_trial["track_list"]
        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(tracknums_data, f, indent=2)
            print("Successfully updated tracknums.json.")
        except Exception as e:
            print(f"Error saving track database: {e}")

    total_run_time = time.time() - start_run_time
    print(f"\nTuning process finished in {total_run_time:.2f}s. Succeeded: {success_count} | Failed: {failure_count}")

    # Write log file (overwrite mode 'w')
    log_path = "tuneext.log"
    print(f"\nWriting tuning log to {log_path}...")
    try:
        with open(log_path, "w", encoding="utf-8") as lf:
            lf.write("=====================================================================\n")
            lf.write(f"Parameter Tuning Log - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            lf.write(f"Mode: {'TEST' if test_mode_active else 'WRITE'}\n")
            lf.write(f"Tuning Strategy: {'HYBRID (BLIND & SUPERVISED)' if blind_active else 'SUPERVISED (CURATED TARGETS)'}\n")
            lf.write(f"Minimum Duration: {args.min_len}\n")
            lf.write(f"Processed: {success_count} succeeded, {failure_count} failed\n")
            lf.write(f"Total time taken: {total_run_time:.2f}s\n")
            lf.write("=====================================================================\n\n")
            
            lf.write("INDIVIDUAL VIDEO TUNING RESULTS:\n")
            lf.write("-"*60 + "\n")
            for vid, best_trial, _, elapsed, target_count in results:
                params_str = f"min_dist={best_trial['min_distance']}s, window={best_trial['adaptive_window']}s, offset={best_trial['offset']}"
                dur_str = candidates[vid][2] if vid in candidates else "Unknown"
                if target_count is not None:
                    lf.write(f"Video: {vid} (Playtime: {dur_str}) | Time: {elapsed:.2f}s | Params: {params_str} | Tracks: Curated {target_count} vs Extracted {best_trial['extracted_count']} (Diff: {best_trial['diff']})\n")
                else:
                    lf.write(f"Video: {vid} (Playtime: {dur_str}) | Time: {elapsed:.2f}s | Params: {params_str} | Tracks: Modal Count {best_trial['extracted_count']} (Unsupervised Grid Consensus Centroid)\n")
            
            if len(results) > 1 and global_rankings:
                lf.write("\n" + "="*60 + "\n")
                lf.write("GLOBAL PARAMETER OPTIMIZATION ANALYSIS\n")
                lf.write("="*60 + "\n")
                if blind_active:
                    has_curated = any(r[4] is not None for r in results)
                    if has_curated:
                        lf.write("Top 3 Optimal Global Parameter Settings (Hybrid Tuning - Combined Diff & Centroid):\n")
                        for rank, rank_data in enumerate(global_rankings[:3], 1):
                            params = rank_data["params"]
                            lf.write(f"  {rank}. Avg Score: {rank_data['avg_score']:.4f} (Max: {rank_data['max_score']:.4f})\n")
                            lf.write(f"     Params: min_dist={params[0]}s, window={params[1]}s, offset={params[2]}\n")
                    else:
                        lf.write("Top 3 Optimal Global Parameter Settings (Grid Consensus Centroid):\n")
                        for rank, rank_data in enumerate(global_rankings[:3], 1):
                            params = rank_data["params"]
                            lf.write(f"  {rank}. Avg Distance to Centroid: {rank_data['avg_score']:.4f} (Max: {rank_data['max_score']:.4f})\n")
                            lf.write(f"     Params: min_dist={params[0]}s, window={params[1]}s, offset={params[2]}\n")
                else:
                    lf.write("Top 3 Optimal Global Parameter Settings (Adaptive Method):\n")
                    for rank, rank_data in enumerate(global_rankings[:3], 1):
                        params = rank_data["params"]
                        lf.write(f"  {rank}. Avg Diff: {rank_data['avg_score']:.2f} tracks (Max Diff: {rank_data['max_score']})\n")
                        lf.write(f"     Params: min_dist={params[0]}s, window={params[1]}s, offset={params[2]}\n")
                lf.write("="*60 + "\n")
    except Exception as e:
        print(f"Warning: Could not write log file {log_path}: {e}")

if __name__ == "__main__":
    main()
