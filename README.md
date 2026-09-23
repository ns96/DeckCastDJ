# DeckCastDJ
A Python/Flask based webapp for sharing YouTube music videos using basic DJ Deck

## Introduction
With the world wide pandemic, I wanted a simple way to share the YouTube music I was playing with family/friends. The obvious approach I tried was just use Zoom/Houseparty etc... and just share the computer audio. This worked ofcourse, but it required folks to use Zoom etc..., and getting good audio quality was a challenge.  Another approach I tried was to use VLC (https://www.videolan.org/vlc/index.html), or Stream What I hear software (https://www.streamwhatyouhear.com/) so that family/friends lessoning just needed a browser on their end. A big downside to this approach is the need to change the ISP firewall settings for streaming to work. Unfortunately, most people don't known how to go about doing this, not to mention the security risk in doing so.

This got me thinking, what I wanted is a web app that someone can go to so they can see what Youtube video I watching. Well it turns out, such web apps already exist, but they are geared towards sharing videos, not music: For music sharing, you really want something like a DJ Deck (https://equipboard.com/posts/best-dj-software) where you can quickly switch between two music videos. More importantly, many of those sites don't work anymore.  I am guessing they violated YouTube's policy at some point.  Additionally, using this approach means music won't be streamed in "realtime", since no actually media content is streamed to the listeners. All that's transfered is data so that a listener's browser loads the correct video from Youtube, commercials and all. This is not a big deal IMO, since we are not talking about developing a professional tool.  Also, testing indicates there is only about a 2 second delay from what the "DJ" is playing to what listeners hears.

## Implementation
Based on the requirements, a workable webapp was built using the YouTube Player API (https://developers.google.com/youtube/iframe_api_reference), Socketio (https://socket.io/), and Python/Flask (https://flask.palletsprojects.com/en/1.1.x/). Everything is put together using less than 1500 lines of Python/HTML/Javascript code, with much of the time looking up how to glue everthing together. The whole stack, was originally hosted on a Rasberry Pi3 running on my home network with a port exposed to the world. Thus far, the code works well, it just needs a nicer UI, maybe a chart box, and the ability to allow multiple "DJs" at the same time by implementing SocketIO "rooms". One thing to keep in mind is that the webapp must be on a publicly facing domain name, otherwise many YouTube videos will not play. I ended up registering a new domain which portforward to the Raspberry Pi3.

## Key Features
* Simultatenously play two YouTube Videos using a slider to control the volume level of each player.
* Cast what the "DJ" is playing to anyone who loads the webapp.
* A Searchable playlist containing all the videos that have been played.
* Loads videos from any public YouTube playlist.
* Ability to create a Que video list and play all back to back
* Basic Automixing of Qued Videos
* Basic pitch control
* Dynamic scrolling marquee displaying the currently playing, previous, and upcoming tracks.
* Track extraction (TN) using audio signal processing with adaptive and fixed peak detection.
* Navigation buttons (Prev / Next) synchronized with tracklist and track number timecodes.
* Basic mobile interface at http://the.server.ip.addr:5054/mobile 

## Track Extraction (TN) Prerequisites & Troubleshooting

The **Track Numbers (TN)** feature automatically downloads audio from YouTube and analyzes energy transitions using SciPy signal processing to detect track changes and timecodes.

### 1. JavaScript Engine Required (Deno or Node.js)
YouTube's streaming endpoints require solving JavaScript signature challenges (n-sig deciphering). To enable `yt-dlp` to download audio streams for track extraction, you must have either **Deno** (recommended) or **Node.js** available:

* **Windows**:
  * **Deno (Recommended)**: Run `winget install DenoLand.Deno` or download `deno.exe` from [deno.com](https://deno.com/) and place it in your system PATH or directly in the DeckCastDJ directory.
  * **Node.js**: Run `winget install OpenJS.NodeJS` or download from [nodejs.org](https://nodejs.org/).
* **macOS**:
  * Run `brew install deno` or `brew install node`.
* **Linux (openSUSE / Ubuntu / Debian)**:
  * **openSUSE**: Run `sudo zypper install nodejs` or install Deno via `curl -fsSL https://deno.land/install.sh | sh`.
  * **Ubuntu / Debian**: Run `sudo apt install nodejs` or install Deno via `curl -fsSL https://deno.land/install.sh | sh`.

> **Note:** DeckCastDJ automatically detects Deno or Node.js if installed in your system PATH, common user directories (`~/.deno/bin`), or placed directly in the application folder (`deno.exe`).

### 2. Audio Processing Tool (`ffmpeg`)
`ffmpeg` is required to convert downloaded audio to mono 11025Hz WAV format for SciPy peak analysis:
* **Windows**: Placed in `C:\Windows\System32\ffmpeg.exe` or bundled next to `DeckCastDJ.exe`.
* **macOS**: `brew install ffmpeg`
* **Linux**: `sudo zypper install ffmpeg` (openSUSE) or `sudo apt install ffmpeg` (Ubuntu/Debian).

### 3. YouTube Bot Detection & Authentication (`cookies.txt`)
Occasionally, YouTube may flag certain IP addresses (especially data centers, cloud servers, or networks with heavy traffic) with automated bot checks, producing errors such as:
* `Sign in to confirm you're not a bot`
* `HTTP Error 429: Too Many Requests`
* `Sign in to confirm your age`

#### How to Fix:
1. In your desktop browser (Chrome, Firefox, Edge, or Brave) where you are logged into YouTube, install the extension **"Get cookies.txt LOCALLY"**.
2. Navigate to [https://www.youtube.com](https://www.youtube.com).
3. Open the extension and export your cookies in standard Netscape format.
4. Save the file as **`cookies.txt`** in the root directory of DeckCastDJ (the same directory as `main.py` or `DeckCastDJ.exe`).

> **Security Note:** `cookies.txt` contains your personal YouTube session tokens. Do **not** commit `cookies.txt` to version control or share it in public software distributions. It is already added to `.gitignore`.

## TO-DOS
* A modern user interface instead if the current barebones interface.
* Implement "rooms" to allow multiple people to use the service.
* Improve Automixing functionality
* Clean up Python code 
* Move away from using pafy which has issues working with the current YouTube API (https://stackoverflow.com/questions/70344739/backend-youtube-dl-py-line-54-in-fetch-basic-self-dislikes-self-ydl-info)
* Make this a more user friendly app that none programmers can deploy, maybe use a VM appliance?

## Legacy pafy Workaround

You must patch the installed `pafy` package inside your active Python environment's `site-packages` directory:

1. **Redirect `pafy` to use `yt-dlp`**
   Locate both `pafy/pafy.py` and `pafy/backend_youtube_dl.py` in your environment's `site-packages` directory. Redirect backend imports from the unmaintained `youtube-dl` library to `yt-dlp` by editing the import line in both files:
   ```diff
   - import youtube_dl
   + import yt_dlp as youtube_dl
   ```

2. **Fix `dislike_count` KeyError**
   To prevent `pafy` from crashing due to YouTube removing public dislike counts, locate lines 53–54 in `pafy/backend_youtube_dl.py` and modify them to use safe `.get()` defaults or just comment out the `self._dislikes` line:
   ```diff
   - self._likes = self._ydl_info['like_count']
   - self._dislikes = self._ydl_info['dislike_count']
   + self._likes = self._ydl_info.get('like_count', 0)
   + self._dislikes = self._ydl_info.get('dislike_count', 0)
   ```
