"""
Created on Wed June 10, 2026

Track extractor module which downloads a youtube video and uses JIT-free 
SciPy spectral analysis to see when the music changes in long video mixes 
then assigns this changes as track 1 ... N for the various sections

@author: Nathan
@version: 0.4.0 (06/10/2026)
"""

import os
import sys
import shutil
import argparse
import glob
import subprocess
import numpy as np
import scipy.signal
from scipy.io import wavfile
from scipy.ndimage import median_filter
import yt_dlp
import json
import threading

# Thread lock for saving/writing data/tracknums.json
save_lock = threading.Lock()

# Global defaults (can be edited to set defaults for script execution)
DEFAULT_VIDEO_ID = "PnGobxRiN88"

# DEFAULT_METHOD: Toggle between 'adaptive' and 'fixed' peak detection
# - 'adaptive': Threshold height adjusts dynamically based on a rolling median of the novelty curve + offset.
# - 'fixed': Standard static peak prominence thresholding.
DEFAULT_METHOD = "adaptive"
#DEFAULT_METHOD = "fixed"

# DEFAULT_MIN_DISTANCE: The minimum separation (in seconds) required between two transition peaks.
# - Lower values (e.g., 30 - 45s) are ideal for fast-paced DJ sets (like Moombahton or quick Dancehall mixes).
# - Higher values (e.g., 90 - 120s) prevent false detections (e.g., mistaking a song's breakdown or build-up as a new track).
DEFAULT_MIN_DISTANCE = 45  

# DEFAULT_PROMINENCE: Only used for 'fixed' method. The static peak prominence threshold (0.0 to 1.0).
DEFAULT_PROMINENCE = 0.035   

# DEFAULT_ADAPTIVE_WINDOW: Only used for 'adaptive' method. The window size in seconds for the rolling median filter.
# - Larger windows (e.g., 120 - 180s) provide a more stable historical baseline.
# - Smaller windows (e.g., 60 - 90s) adjust quickly to rapid dynamic changes, but might be influenced by longer song builds.
DEFAULT_ADAPTIVE_WINDOW = 150

# DEFAULT_THRESHOLD_OFFSET: Only used for 'adaptive' method. The height offset added to the rolling median threshold.
# - Lower values (e.g., 0.03 - 0.04) are more sensitive and catch subtle transitions.
# - Higher values (e.g., 0.05 - 0.07) are more conservative and filter out local noise.
DEFAULT_THRESHOLD_OFFSET = 0.04

def format_timestamp(seconds):
    """Converts seconds into HH:MM:SS or MM:SS format."""
    sec = int(round(seconds))
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    secs = sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes:02d}:{secs:02d}"

def get_audio_file(video_id):
    """
    Checks if the cached WAV file (ytd/<video_id>_11k.wav) already exists.
    If yes, returns it.
    If not, downloads the raw audio via yt-dlp, converts it to 11k WAV,
    deletes the raw download, and returns the path to the WAV file.
    """
    os.makedirs("ytd", exist_ok=True)
    
    wav_path = os.path.join("ytd", f"{video_id}_11k.wav")
    if os.path.exists(wav_path):
        print(f"Found cached WAV file: {wav_path}")
        return wav_path
        
    print(f"Cached WAV file not found. Fetching info and downloading YouTube ID: {video_id}...")
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        # Search candidate locations for cookies.txt
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidate_cookies = [
            os.path.join(script_dir, "cookies.txt"),
            os.path.join(os.getcwd(), "cookies.txt"),
            os.path.join(script_dir, "ytd", "cookies.txt"),
        ]
        cookie_file = next((p for p in candidate_cookies if os.path.isfile(p)), None)

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join('ytd', f'{video_id}.%(ext)s'),
            'quiet': False,
            'no_warnings': True,
            'remote_components': ['ejs:github', 'ejs:npm'],
            'extractor_args': {
                'youtube': {
                    'player_client': ['web', 'android', 'ios']
                }
            },
        }

        # Search candidate locations for Deno or Node JS runtime (Windows, macOS, Linux)
        deno_candidates = [
            shutil.which("deno"),
            os.path.join(script_dir, "deno.exe"),
            os.path.join(script_dir, "deno"),
            os.path.expandvars(r"%USERPROFILE%\.deno\bin\deno.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\deno\deno.exe"),
            os.path.expanduser("~/.deno/bin/deno"),
            "/opt/homebrew/bin/deno",  # Apple Silicon Mac
            "/usr/local/bin/deno",     # Intel Mac / Linux
            "/usr/bin/deno",           # Linux package manager
        ]
        deno_path = next((p for p in deno_candidates if p and os.path.isfile(p)), None)
        if deno_path:
            ydl_opts['js_runtimes'] = {'deno': {'path': deno_path}}
        else:
            # Fall back to Node.js if present (very common on Linux servers)
            node_candidates = [
                shutil.which("node"),
                "/usr/bin/node",
                "/usr/local/bin/node",
            ]
            node_path = next((p for p in node_candidates if p and os.path.isfile(p)), None)
            if node_path:
                ydl_opts['js_runtimes'] = {'node': {'path': node_path}}

        if cookie_file:
            print(f"Using YouTube cookies from: {cookie_file}")
            ydl_opts['cookiefile'] = cookie_file
        else:
            print("Note: No 'cookies.txt' found in project root. If YouTube triggers bot checks, add cookies.txt.")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Unknown Title')
            duration = info.get('duration', 0)
            ext = info.get('ext', 'm4a')
            raw_filepath = os.path.join("ytd", f"{video_id}.{ext}")
            
            print(f"\nTitle: {title}")
            print(f"Duration: {format_timestamp(duration)}")
            print("Download complete.")
            
            print(f"Converting audio to standard mono 11025Hz WAV using ffmpeg...")
            ffmpeg_bin = "ffmpeg.exe" if os.path.isfile("ffmpeg.exe") else "ffmpeg"
            cmd = f'"{ffmpeg_bin}" -i "{raw_filepath}" -ar 11025 -ac 1 "{wav_path}" -y -loglevel quiet'
            subprocess.run(cmd, shell=True, check=True)
            
            # Delete the raw downloaded audio file
            if os.path.exists(raw_filepath):
                try:
                    os.remove(raw_filepath)
                except Exception as e:
                    print(f"Note: Could not delete raw file {raw_filepath}: {e}")
            
            return wav_path
    except Exception as e:
        err_str = str(e)
        if "Sign in to confirm" in err_str or "bot" in err_str.lower() or "429" in err_str:
            target_cookie_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
            print("\n" + "=" * 72)
            print("[YOUTUBE BOT DETECTION / AUTHENTICATION REQUIRED]")
            print("YouTube is blocking automated requests from your IP address:")
            print(f"  {err_str}")
            print("\nHow to fix:")
            print("1. In your browser (where you are logged into YouTube), install")
            print("   the extension 'Get cookies.txt LOCALLY'.")
            print("2. Navigate to https://www.youtube.com and export your cookies.")
            print(f"3. Save the exported file as 'cookies.txt' at:")
            print(f"   {target_cookie_path}")
            print("4. (If format/signature error also occurs) Ensure Deno or Node.js is installed")
            print("   so yt-dlp can solve YouTube JS challenges.")
            print("=" * 72 + "\n")
        raise RuntimeError(f"Error fetching/downloading video from YouTube: {e}")

def compute_novelty_curve(features, W):
    """
    Computes Foote's novelty curve on the feature matrix using cosine distance.
    Fully JIT-free and vectorized using cumulative sum moving averages in float32.
    """
    d, T = features.shape
    
    # Pad features to handle start/end boundary frames nicely
    features_padded = np.pad(features, ((0, 0), (W, W)), mode='edge')
    cumsum = np.cumsum(features_padded, axis=1, dtype=np.float32)
    del features_padded
    
    # Left window averages for padded index range [t, t + W - 1]
    left_means = (cumsum[:, W:T+W] - cumsum[:, 0:T]) / np.float32(W)
    # Right window averages for padded index range [t + W, t + 2*W - 1]
    right_means = (cumsum[:, 2*W:T+2*W] - cumsum[:, W:T+W]) / np.float32(W)
    del cumsum
    
    # Cosine distance computation: 1.0 - (A . B) / (||A|| * ||B||)
    dot_products = np.sum(left_means * right_means, axis=0)
    norms_left = np.linalg.norm(left_means, axis=0)
    norms_right = np.linalg.norm(right_means, axis=0)
    del left_means, right_means
    
    # Avoid division by zero
    norms_left[norms_left == 0] = 1e-10
    norms_right[norms_right == 0] = 1e-10
    
    cosine_similarity = dot_products / (norms_left * norms_right)
    cosine_similarity = np.clip(cosine_similarity, -1.0, 1.0)
    novelty = 1.0 - cosine_similarity
    return novelty

def segment_audio(wav_path, min_distance_sec, method=DEFAULT_METHOD, prominence=DEFAULT_PROMINENCE, adaptive_window_sec=DEFAULT_ADAPTIVE_WINDOW, threshold_offset=DEFAULT_THRESHOLD_OFFSET):
    """
    Loads downsampled WAV file, calculates transition novelty via STFT,
    and returns list of transition timestamps.
    """
    print(f"Loading downsampled WAV file: {wav_path}...")
    sr, y = wavfile.read(wav_path)
    
    # Convert to mono if it's stereo (fallback check)
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
    print(f"Audio loaded. Duration: {format_timestamp(duration)}")
    
    print("Computing Short-Time Fourier Transform (STFT)...")
    n_fft = 2048
    hop_length = 512
    win = scipy.signal.get_window('hann', n_fft).astype(np.float32)
    
    # Compute STFT using scipy with single-precision float32 window to prevent float64 promotion
    frequencies, times, Zxx = scipy.signal.stft(
        y, 
        fs=sr, 
        window=win,
        nperseg=n_fft, 
        noverlap=n_fft - hop_length
    )
    del y
    magnitude = np.abs(Zxx)
    del Zxx
    
    # Use log-magnitude spectrogram for structural analysis
    features = np.log1p(magnitude * 100)
    del magnitude
    
    # Sliding block size: 10 seconds
    window_sec = 10
    W = int(round(window_sec * sr / hop_length))
    print(f"Analyzing transitions with a sliding window of {window_sec} seconds (W={W} frames)...")
    
    novelty = compute_novelty_curve(features, W)
    del features
    
    # Track separation constraints:
    # Minimum track length (in frames)
    min_dist_frames = int(round(min_distance_sec * sr / hop_length))
    
    # Find local maxima peaks in the novelty curve based on the chosen method
    if method == "adaptive":
        # Calculate local median filter size in frames
        kernel_size = int(round(adaptive_window_sec * sr / hop_length))
        if kernel_size % 2 == 0:
            kernel_size += 1
            
        # Calculate adaptive threshold curve
        local_median = median_filter(novelty, size=kernel_size)
        threshold_curve = local_median + threshold_offset
        
        # Find peaks exceeding the local threshold height
        peaks, _ = scipy.signal.find_peaks(
            novelty, 
            distance=min_dist_frames, 
            height=threshold_curve
        )
        print(f"Applying adaptive threshold (window={adaptive_window_sec}s, offset={threshold_offset}). Found {len(peaks)} candidate transitions.")
    else:  # "fixed"
        # Find peaks using standard global prominence threshold
        peaks, _ = scipy.signal.find_peaks(
            novelty, 
            distance=min_dist_frames, 
            prominence=prominence
        )
        print(f"Applying fixed prominence threshold ({prominence}). Found {len(peaks)} candidate transitions.")
    
    # Convert peak frame indices to timestamps (seconds)
    timestamps = times[peaks]
    
    # Filter out transitions too close to the start or end of the track to avoid false positives (e.g., final fade-out)
    valid_transitions = [t for t in timestamps if t > 10 and t < duration - 20]
        
    return valid_transitions

def extract_tracks(video_id, delete=False, method=DEFAULT_METHOD, 
                   min_distance=DEFAULT_MIN_DISTANCE, prominence=DEFAULT_PROMINENCE, 
                   adaptive_window=DEFAULT_ADAPTIVE_WINDOW, offset=DEFAULT_THRESHOLD_OFFSET,
                   save=True):
    """
    Downloads (if not cached), segments the mix, compiles the tracklist,
    saves the result to data/tracknums.json, and returns the tracklist.
    """
    # Download or get audio file path
    filepath = get_audio_file(video_id)
    
    try:
        # Segment mix and find transition boundaries
        transitions = segment_audio(
            filepath, 
            min_distance_sec=min_distance,
            method=method,
            prominence=prominence,
            adaptive_window_sec=adaptive_window,
            threshold_offset=offset
        )
        
        # Build tracklist output and prepare list for JSON saving
        print("\n" + "="*40)
        print("GENERATED TRACKLIST")
        print("="*40)
        
        # All mixes start with Track 1 at 00:00
        print(f"00:00 Track 1")
        track_list = ["00:00 Track 1"]
        
        track_num = 2
        for t in transitions:
            # Avoid placing a peak at the absolute start
            if t > 10:
                track_str = f"{format_timestamp(t)} Track {track_num}"
                print(track_str)
                track_list.append(track_str)
                track_num += 1
        print("="*40 + "\n")
        
        # Save results to data/tracknums.json
        if save:
            with save_lock:
                os.makedirs("data", exist_ok=True)
                json_path = os.path.join("data", "tracknums.json")
                
                # Load existing database if it exists
                tracknums_data = {}
                if os.path.exists(json_path):
                    try:
                        with open(json_path, 'r', encoding='utf-8') as f:
                            tracknums_data = json.load(f)
                    except Exception as e:
                        print(f"Warning: Could not parse existing {json_path} ({e}). Starting fresh.")
                        tracknums_data = {}
                        
                # Update record for the current video
                tracknums_data[video_id] = track_list
                
                # Write updated database back to disk
                try:
                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump(tracknums_data, f, indent=2)
                    print(f"Saved track listing to {json_path}.")
                except Exception as e:
                    print(f"Error saving track listing to {json_path}: {e}")
            
        return track_list
        
    finally:
        # Perform cleanup if requested
        if delete:
            if os.path.exists(filepath):
                print(f"Cleaning up: deleting {filepath}...")
                try:
                    os.remove(filepath)
                    print("File deleted.")
                except Exception as e:
                    print(f"Error deleting file: {e}")

def main():
    parser = argparse.ArgumentParser(
        description="Extract track transition timestamps from a YouTube video mix using JIT-free SciPy."
    )
    parser.add_argument(
        "video_id", 
        nargs="?", 
        default=DEFAULT_VIDEO_ID, 
        help=f"YouTube Video ID (default: {DEFAULT_VIDEO_ID})"
    )
    parser.add_argument(
        "--delete", 
        action="store_true", 
        help="Delete the downloaded audio file in ytd/ after processing (default: keep/cache it)"
    )
    parser.add_argument(
        "--method",
        choices=["fixed", "adaptive"],
        default=DEFAULT_METHOD,
        help=f"Transition detection method: 'adaptive' or 'fixed' (default: {DEFAULT_METHOD})"
    )
    parser.add_argument(
        "--min-distance",
        type=int,
        default=DEFAULT_MIN_DISTANCE,
        help=f"Minimum track separation in seconds (default: {DEFAULT_MIN_DISTANCE})"
    )
    parser.add_argument(
        "--prominence",
        type=float,
        default=DEFAULT_PROMINENCE,
        help=f"Peak detection prominence threshold for 'fixed' method (default: {DEFAULT_PROMINENCE})"
    )
    parser.add_argument(
        "--adaptive-window",
        type=int,
        default=DEFAULT_ADAPTIVE_WINDOW,
        help=f"Adaptive threshold rolling median window in seconds (default: {DEFAULT_ADAPTIVE_WINDOW})"
    )
    parser.add_argument(
        "--offset",
        type=float,
        default=DEFAULT_THRESHOLD_OFFSET,
        help=f"Adaptive threshold height offset above median (default: {DEFAULT_THRESHOLD_OFFSET})"
    )
    
    args = parser.parse_args()
    
    try:
        extract_tracks(
            video_id=args.video_id,
            delete=args.delete,
            method=args.method,
            min_distance=args.min_distance,
            prominence=args.prominence,
            adaptive_window=args.adaptive_window,
            offset=args.offset
        )
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
