"""
Created on Wed June 10, 2026

Track extractor module which downloads a youtube video and uses JIT-free 
SciPy spectral analysis to see when the music changes in long video mixes 
then assigns this changes as track 1 ... N for the various sections

@author: Nathan
@version: 0.3.0 (06/10/2026)
"""

import os
import sys
import argparse
import glob
import subprocess
import numpy as np
import scipy.signal
from scipy.io import wavfile
import yt_dlp

# Global defaults (can be edited to set defaults for script execution)
DEFAULT_VIDEO_ID = "1Tn1S86A44Y"

# DEFAULT_MIN_DISTANCE: The minimum separation (in seconds) required between two transition peaks.
# - Lower values (e.g., 30 - 45s) are ideal for fast-paced DJ sets (like Moombahton or quick Dancehall mixes).
# - Higher values (e.g., 90 - 120s) prevent false detections (e.g., mistaking a song's breakdown or build-up as a new track).
DEFAULT_MIN_DISTANCE = 25   

# DEFAULT_PROMINENCE: The peak detection prominence threshold (how distinct a peak must be relative to its local basin).
# - Novelty score distance ranges from 0.0 to 1.0.
# - Lower values (e.g., 0.04 - 0.06) are highly sensitive, capturing smooth crossfades and beat-matched/same-riddim song transitions, but may introduce false positives during quiet parts.
# - Higher values (e.g., 0.08 - 0.12) only capture prominent transitions (e.g., sudden genre/key cuts or distinct tempo shifts).
DEFAULT_PROMINENCE = 0.043   

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
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join('ytd', f'{video_id}.%(ext)s'),
            'quiet': False,
            'no_warnings': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Unknown Title')
            duration = info.get('duration', 0)
            ext = info.get('ext', 'm4a')
            raw_filepath = os.path.join("ytd", f"{video_id}.{ext}")
            
            print(f"\nTitle: {title}")
            print(f"Duration: {format_timestamp(duration)}")
            print("Download complete.")
            
            # Convert to standard mono 11025Hz WAV
            print(f"Converting audio to standard mono 11025Hz WAV using ffmpeg...")
            cmd = f'ffmpeg -i "{raw_filepath}" -ar 11025 -ac 1 "{wav_path}" -y -loglevel quiet'
            subprocess.run(cmd, shell=True, check=True)
            
            # Delete the raw downloaded audio file
            if os.path.exists(raw_filepath):
                try:
                    os.remove(raw_filepath)
                except Exception as e:
                    print(f"Note: Could not delete raw file {raw_filepath}: {e}")
            
            return wav_path
    except Exception as e:
        print(f"Error fetching/downloading video from YouTube: {e}")
        sys.exit(1)

def compute_novelty_curve(features, W):
    """
    Computes Foote's novelty curve on the feature matrix using cosine distance.
    Fully JIT-free and vectorized using cumulative sum moving averages.
    """
    d, T = features.shape
    
    # Pad features to handle start/end boundary frames nicely
    features_padded = np.pad(features, ((0, 0), (W, W)), mode='edge')
    cumsum = np.cumsum(features_padded, axis=1)
    
    # Left window averages for padded index range [t, t + W - 1]
    left_means = (cumsum[:, W:T+W] - cumsum[:, 0:T]) / W
    # Right window averages for padded index range [t + W, t + 2*W - 1]
    right_means = (cumsum[:, 2*W:T+2*W] - cumsum[:, W:T+W]) / W
    
    # Cosine distance computation: 1.0 - (A . B) / (||A|| * ||B||)
    dot_products = np.sum(left_means * right_means, axis=0)
    norms_left = np.linalg.norm(left_means, axis=0)
    norms_right = np.linalg.norm(right_means, axis=0)
    
    # Avoid division by zero
    norms_left[norms_left == 0] = 1e-10
    norms_right[norms_right == 0] = 1e-10
    
    cosine_similarity = dot_products / (norms_left * norms_right)
    cosine_similarity = np.clip(cosine_similarity, -1.0, 1.0)
    novelty = 1.0 - cosine_similarity
    return novelty

def segment_audio(wav_path, min_distance_sec, prominence):
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
    
    # Compute STFT using scipy
    frequencies, times, Zxx = scipy.signal.stft(
        y, 
        fs=sr, 
        nperseg=n_fft, 
        noverlap=n_fft - hop_length
    )
    magnitude = np.abs(Zxx)
    
    # Use log-magnitude spectrogram for structural analysis
    features = np.log1p(magnitude * 100)
    
    # Sliding block size: 10 seconds
    window_sec = 10
    W = int(round(window_sec * sr / hop_length))
    print(f"Analyzing transitions with a sliding window of {window_sec} seconds (W={W} frames)...")
    
    novelty = compute_novelty_curve(features, W)
    
    # Track separation constraints:
    # Minimum track length (in frames)
    min_dist_frames = int(round(min_distance_sec * sr / hop_length))
    
    # Find local maxima peaks in the novelty curve
    peaks, _ = scipy.signal.find_peaks(
        novelty, 
        distance=min_dist_frames, 
        prominence=prominence
    )
    
    # Convert peak frame indices to timestamps (seconds)
    timestamps = times[peaks]
        
    return list(timestamps)

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
        "--min-distance",
        type=int,
        default=DEFAULT_MIN_DISTANCE,
        help=f"Minimum track separation in seconds (default: {DEFAULT_MIN_DISTANCE})"
    )
    parser.add_argument(
        "--prominence",
        type=float,
        default=DEFAULT_PROMINENCE,
        help=f"Peak detection prominence threshold (default: {DEFAULT_PROMINENCE})"
    )
    
    args = parser.parse_args()
    
    # Download or get audio file path
    filepath = get_audio_file(args.video_id)
    
    try:
        # Segment mix and find transition boundaries
        transitions = segment_audio(
            filepath, 
            args.min_distance, 
            args.prominence
        )
        
        # Build tracklist output
        print("\n" + "="*40)
        print("GENERATED TRACKLIST")
        print("="*40)
        
        # All mixes start with Track 1 at 00:00
        print(f"00:00 Track 1")
        
        track_num = 2
        for t in transitions:
            # Avoid placing a peak at the absolute start
            if t > 10:
                print(f"{format_timestamp(t)} Track {track_num}")
                track_num += 1
        print("="*40 + "\n")
        
    finally:
        # Perform cleanup if requested
        if args.delete:
            if os.path.exists(filepath):
                print(f"Cleaning up: deleting {filepath}...")
                try:
                    os.remove(filepath)
                    print("File deleted.")
                except Exception as e:
                    print(f"Error deleting file: {e}")

if __name__ == "__main__":
    main()
