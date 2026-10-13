# DeckCastDJ
A Python/Flask based webapp for sharing YouTube music videos using basic DJ Deck

## Introduction
With the world wide pandemic, I wanted a simple way to share the YouTube music I was playing with family/friends. The obvious approach I tried was just use Zoom/Houseparty etc... and just share the computer audio. This worked ofcourse, but it required folks to use Zoom etc..., and getting good audio quality was a challenge.  Another approach I tried was to use VLC (https://www.videolan.org/vlc/index.html), or Stream What I hear software (https://www.streamwhatyouhear.com/) so that family/friends lessoning just needed a browser on their end. A big downside to this approach is the need to change the ISP firewall settings for streaming to work. Unfortunately, most people don't known how to go about doing this, not to mention the security risk in doing so.

This got me thinking, what I wanted is a web app that someone can go to so they can see what Youtube video I watching. Well it turns out, such web apps already exist, but they are geared towards sharing videos, not music: For music sharing, you really want something like a DJ Deck (https://equipboard.com/posts/best-dj-software) where you can quickly switch between two music videos. More importantly, many of those sites don't work anymore.  I am guessing they violated YouTube's policy at some point.  Additionally, using this approach means music won't be streamed in "realtime", since no actually media content is streamed to the listeners. All that's transfered is data so that a listener's browser loads the correct video from Youtube, commercials and all. This is not a big deal IMO, since we are not talking about developing a professional tool.  Also, testing indicates there is only about a 2 second delay from what the "DJ" is playing to what listeners hears.

## Implementation
Based on the requirements, a workable webapp was built using the YouTube Player API (https://developers.google.com/youtube/iframe_api_reference), Socketio (https://socket.io/), and Python/Flask (https://flask.palletsprojects.com/en/1.1.x/). Everything is put together using less than 1500 lines of Python/HTML/Javascript code, with much of the time looking up how to glue everthing together. The whole stack, was originally hosted on a Rasberry Pi3 running on my home network with a port exposed to the world. Thus far, the code works well, it just needs a nicer UI, maybe a chart box, and the ability to allow multiple "DJs" at the same time by implementing SocketIO "rooms". One thing to keep in mind is that the webapp must be on a publicly facing domain name, otherwise many YouTube videos will not play. I ended up registering a new domain which portforward to the Raspberry Pi3.

## Project Layout

```
DeckCastDJ/
  main.py         Flask/SocketIO entry point and DJ logic
  config.py       local configuration (API key, playlists, port)
  src/            application modules
    trackext.py     Track Number (TN) extraction
    acquire.py      audio acquisition for the CD burner
    cdburn.py       CD project store, disc planning and burning
  tools/          developer utilities - the server does not need them to run
    batchext.py              offline batch TN extraction
    tunext.py                offline TN parameter tuning
    prepare_clean_distro.py  build-time clean distribution prep
    make_release.py          build the server release zip
  templates/  static/  data/  ytd/
```

Only `main.py` and `config.py` sit at the root, so an update is just those two
files plus the `src/`, `templates/` and `static/` folders.

## Music library host

The `/mp3` page does not hold the library itself - it reads the whole database
(`player/audiodb.txt`) and streams every track from the music host over HTTP, so
**the browser** must be able to reach that host, not the machine running the
app. A host that has moved, or a network that cannot route to it, shows up as an
empty MP3 list.

The default is `http://pi86.sytes.net/music/`. To point somewhere else - a LAN
address, a different port, a new hostname - put the base URL in
`data/settings.json` (create the file if needed):

```json
{ "musicBaseUrl": "http://192.168.1.120/music/" }
```

The value is used for both the page and the burner's "music" track source, and
`main.py` injects it into the page, so there is no URL to hunt for in the
templates. The URL must end in `/`. If the page cannot load the database it says
so in the footer and in the directory box instead of silently listing nothing.

## Deploying to the server

`tools/make_release.py` packs the code into one zip for a server update:

```
python tools/make_release.py
# -> dist/DeckCastDJ-2.7.10-2026-10-13.zip   (23 files, 2.2 MB)
```

The archive is code only, so per-machine state survives the update:

* `data/` - playlists, track numbers, bookmarks, saved CD projects
* `ytd/` - downloaded sources and premastered WAVs
* `config.py` - holds the YouTube API key

The one exception is `cookies.txt`: it is not code, but it is what keeps the
server's yt-dlp requests from being refused, and re-exporting it by hand after
every update is busywork - so it is packed whenever it exists. That makes the zip
a credential file, so keep it off shared storage and do not pass it around. When
the file is missing the script says so explicitly and the archive simply carries
no YouTube credentials.

`__pycache__`, `*_old.html` and the developer build files are skipped. `static/`
*is* copied in full, because the generated QR images are needed at run time even
though git ignores them. The folder list is explicit in the script, so nothing
new ships by accident.

Copy the zip over, unzip it on top of the existing install and restart:

```
scp dist/DeckCastDJ-2.7.10-2026-10-13.zip user@host:~/
ssh user@host
unzip -o ~/DeckCastDJ-2.7.10-2026-10-13.zip -d ~/DeckCastDJ
systemctl restart deckcast          # or: kill + nohup python3.11 main.py &
```

Keeping the previous zip is the rollback - re-copy it and restart. `config.py` is
never overwritten, so the builder prints the settings the code imports
(`app_port`, `youtubeApiKey`, `useYoutube`, `youtubePL`); if a release adds a new
one, add it to the server's copy by hand.

`--no-tools` leaves `tools/` out, and `--dry-run` lists what would be packed
without writing anything.

The archive carries `tools/`, so the offline utilities run wherever you unzip it
- there is no need to hand-copy them to a second machine:

```
python3 tools/tunext.py --video <videoId>   # tune one video, saves data/tracknums.json
python3 tools/tunext.py                     # tune every video in data/playlist.json
```

`tunext.py` needs `numpy`, `scipy` and - through `src/trackext.py` - the Track
Extraction prerequisites below (`yt-dlp`, `ffmpeg`, Deno or Node). It reads
`data/playlist.json`, `data/userPlaylist.json` and `data/tracklists.json` when
they exist and creates `data/` itself when saving, so a bare unzip still tunes a
video you name with `--video`. Bring `data/tracknums.json` back to the server
afterwards to use the results.

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
* CD burning: build a disc-sized playlist from YouTube (or the MP3 library) and burn an audio CD at http://127.0.0.1:5054/burner (on the machine running DeckCastDJ - see the CD Burning notes)
* Que downloads: the **Download MP3** button under the DJ que table writes every queued video to `ytd/mp3/` as a 192 kbps MP3 (see below)

### Que downloads (MP3)

Below the DJ que table sit three buttons: **Check**, **Burn** and **Download MP3**. They stay hidden until the que actually holds a video, so an empty que leaves the page tidy. **Download MP3** walks the que and writes each video to `ytd/mp3/<videoId> - <title>.mp3` on the machine running the app - one yt-dlp download per video, converted to MP3 by ffmpeg (so ffmpeg and the Track Extraction prerequisites above apply).

* Videos already downloaded are **skipped**, matched on the video id, so an upstream title change neither re-downloads nor duplicates them.
* Progress shows three ways: a small **progress card** in the bottom-right corner (current video, an overall bar, and running totals - closable with its **X**), the **Console** line (`Downloading 3 / 12 - Title (45%)`), and the terminal, which logs each video and every 10% of it. The three buttons disable until the job finishes, and the final line reports how many were downloaded, skipped and failed; failures go to the terminal with the full reason.
* One download runs at a time: press it again while a download is in progress and it says so instead of starting a second job.
* It is **local-only**, exactly like the burner: the files are written to the machine running DeckCastDJ, so a remote client is refused with an explanation. Add `"burnerAllowRemote": true` to `data/settings.json` to open it to a trusted network, or tunnel the port.
* These files are keepers, not cache: **Clear CD Cache** never deletes them (it only sweeps `ytd/cd/`, `ytd/music_src/` and the `*_src.*` staging files).
* The que belongs to the browser connection that built it (that is how the DJ page has always scoped que lists), so Download MP3 only ever processes the que you can see.

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

The check is about the *session*, not the IP: YouTube accepts the request once it can see a signed-in account.

DeckCastDJ reads credentials from **`cookies.txt` and nothing else** - it never opens the cookie database of an installed browser. That keeps the credential to one file you control, and it means the app never touches another application's profile.

#### Exporting `cookies.txt`
1. In your desktop browser (Chrome, Firefox, Edge, or Brave) where you are logged into YouTube, install the extension **"Get cookies.txt LOCALLY"**.
2. Navigate to [https://www.youtube.com](https://www.youtube.com).
3. Open the extension and export your cookies in standard Netscape format.
4. Save the file as **`cookies.txt`** next to the app - beside `main.py`, or beside `DeckCastDJ.exe` in a packaged build. `ytd/cookies.txt` and the working directory are also searched, but the app folder is where it belongs.

The file must contain the sign-in cookies **`SID`, `HSID`, `SSID` and `LOGIN_INFO`**. If it does not, the export came from a signed-out browser and YouTube will still refuse - the app prints a warning naming the file when it spots that.

yt-dlp rewrites whatever cookie file it is handed, so DeckCastDJ gives it a copy (`ytd/.youtube-cookies.txt`) instead and hands that over. The copy is re-made whenever it stops matching your export, which matters more than it sounds: a run that ends early (a bot check, a geo error) writes back only the few cookies it saw, and an anonymous copy would then be re-used forever. That is the state the app gets stuck in when you see the bot check even though `cookies.txt` is fine - deleting `ytd/.youtube-cookies.txt`, or touching `cookies.txt`, makes it re-copy.

Every YouTube call goes through this: the burner's **Acquire Audio**, Track Numbers extraction, and the lookups behind the DJ page (pafy), so there is one place to fix authentication rather than three. If YouTube starts refusing again, the export has usually gone stale - log out and back in, or just re-export it and replace the file.

`cookies.txt` is optional: without it the app runs normally, requests to YouTube are simply anonymous (and the app says so in the log).

> **Security Note:** `cookies.txt` contains your personal YouTube session tokens. Do **not** commit `cookies.txt` to version control or share it in public software distributions. It is already added to `.gitignore`. The server release zip and the developer standalone builds do pack it, because the target machine is your own - which makes those archives credential files too. A de-personalized build (`build.bat -clean` / `build.sh -clean`) deliberately leaves it out.

## CD Burning (`/burner`)

The burner page builds a playlist that fits on an audio CD (**Red Book**) and writes it using the operating system's built-in burner.

The page - and every action in it - is restricted to `127.0.0.1`, because the drive belongs to the machine running DeckCastDJ (see the last note below for the remote-workstation options).

### Getting tracks onto the burner
Three ways:
1. **YouTube URL** - paste a single video or a playlist URL into the burner page and press **Add**.
2. **Burn (DJ page)** - the **Burn** button next to **Check** on the DJ que list pushes the whole queue over.
3. **Burn to CD (MP3 page)** - pushes the queued tracks from the `/mp3` music library.

### Building and burning
1. `1 · Acquire Audio` - downloads each source (`yt-dlp` for YouTube, HTTP for the music library) and converts it to **44.1 kHz / 16-bit / stereo PCM**, in a single pass.
2. Pick the disc to write from the **Disc** dropdown, which lists every disc in the plan with its size and whether it is already burned.
3. `2 · Test Burn` - asks the drive to run the whole burn without writing anything, so the disc stays blank and can be used afterwards. It takes about as long as a real burn and shows the same live progress, which makes it the way to check a disc (and the progress display) without spending one. With no blank disc inserted it says so and touches nothing - and since IMAPI2 (Windows) has no test mode, Windows runs the **audio check** instead: every track is read, its samples located and its sector padding worked out, which is everything a burn does to the audio, with no disc needed and nothing written.
4. `Burn Disc` - asks for confirmation and writes the selected disc. It refuses - touching nothing - if the disc's folder is not exactly what the page lists (the log names the files that disagree), or if any track's audio cannot be read as 44.1 kHz 16-bit stereo PCM in full.

Discs can be written in any order, and the same disc can be written again (handy when a burn fails or when you want a spare copy). The dropdown follows the plan - it starts on the first disc that still needs writing and steps forward as discs are burned - until you pick one yourself, and then your choice sticks. Burned marks are remembered when a project is saved, loaded or the app is restarted, and are cleared if you change the layout so that disc no longer holds the same audio (in which case it needs writing again).

With **Delete downloaded audio after a successful burn** ticked, only the disc that was just written is cleared - the audio of the discs still waiting its turn is kept, so a multi-disc set can be written one disc at a time.

The disc plan splits long lists automatically: **79:30 of audio per 80-minute disc** (73:30 for a 74-minute blank), **99 max tracks**, and as many discs as needed. Overflow simply moves to the next disc - no track is dropped.

### Prerequisites
* **ffmpeg** on the PATH (or next to the app) - used to premaster and to probe durations. See the Track Extraction section above for install notes.
* An **external USB optical drive** - no Mac since roughly 2016 has a built-in drive.
* **Nothing else.** Burning uses the writer the OS already ships:
  * **macOS** - `drutil` (no install, no root).
  * **Windows** - the built-in IMAPI2 API (no install).

  No third-party burner (cdrecord, cdrdao, ImgBurn, ...) is required and no admin/root privileges are needed.

### Notes & limitations
* **Audio CD only.** Tracks are burned as uncompressed PCM so the disc plays in any CD player, including a car stereo.
* **No CD-TEXT.** Neither native writer can write disc titles, so titles live in the premastered WAV file names (`01 - Artist - Title.wav`), which also sets the burn order.
* **The disc's track order is fixed before the drive is touched.** The names carry the track numbers (`01 - Artist - Title.wav`, numbered per disc, zero padded) and that is the order: Windows sorts the names and burns them in that order, and **macOS is not left to a folder at all** - the manual says drutil burns a directory of audio "in alphabetical order", but a directory is read in the file system's own order, and a disc burned that way came out with the page's track 8 as its track 1 (two tracks short as well). So every macOS burn first writes the disc's layout next to the audio (`disc1/DeckCastDJ.toc`, `CD_DA` plus one `TRACK AUDIO`/`FILE` per track) and hands *that* to drutil: it burns the files the layout names, in the order it names them, and nothing else in the folder can become a track. If drutil refuses the layout, the folder burn is used instead and the log says so, because that order is the file system's and not the plan's. The layout is also what the log prints as the disc's track order, so track 1 is never a surprise, and after the burn the app asks the drive what is on the disc and prints that too - the drive's own answer, next to the app's.
* **The audio is checked before it is burned, not after.** A file that cannot be read as 44.1 kHz 16-bit stereo PCM - because it is truncated, or is not what its header claims - is not written to the disc and the burner says nothing about it: the disc simply comes out with fewer tracks than the page lists. Every planned track is therefore read first (header, format and the declared audio against the file's real size) and **Burn Disc** refuses by name instead, telling you to remove that file and run **1 · Acquire Audio** again. A burn that comes back having written fewer tracks than planned also warns, naming the tracks it never announced.
* **A disc is checked against the plan before anything is written.** Any file in the folder the page does not know about, and any other audio in there (`.aiff`, `.m4a`, `.mp3`, ...), is refused by name rather than burned, since a file the page does not list is a track it does not show - which is how a disc ends up whose track 1 is not the page's track 1. **1 · Acquire Audio** rebuilds the folder: it deletes what the plan no longer uses and keeps the audio that is already downloaded.
* **Track preview works in a packaged build too.** Flask sends a file by resolving a *relative* path against the application's root, and in a frozen build that root is the bundle folder (`_internal`, next to the executable) rather than the folder the app runs from - where the media tree is. So the preview hands Flask an absolute path, because otherwise the audio is plainly there in `ytd/cd/...` and the page still says it cannot find it. A preview that really cannot find a track now says which file it looked for (`That track has not been downloaded yet (ytd/cd/<project>/disc1/02 - Artist - Title.wav).`), and the app prints its app and media folders at startup, so this is never a guessing game again.
* **Media must be blank.** A non-blank disc is reported and the burn is refused rather than risk a coaster.
* **Downloaded originals are not kept.** Each track's download is deleted as soon as its CD-ready WAV exists, so only `ytd/cd/<project>/discN/` holds burner media. That applies to both providers: YouTube sources land in `ytd/` and music-host sources in `ytd/music_src/`, and the subfolders a music-host download created are pruned once they are empty. Re-running **Acquire Audio** skips every track that already has a WAV. If `ffmpeg` fails on a track, its original is left in place so a retry does not re-download it. Files the app did not download - anything already sitting under `ytd/music/`, which is where old versions staged these - are reused but never deleted automatically.
* **The burner page catches up after a disconnect.** A background tab can be throttled and dropped by the server while a job carries on regardless. The header shows **Live** or **Disconnected - reconnecting…**, and on reconnect the page replays the log lines it missed, so the on-screen log matches the terminal.
* **The page footer shows the instance id** (also printed as `DeckCastDJ instance ... listening on port ...` at startup). If the two do not match, the page is talking to a different copy of the app than the terminal is.
* **Every burn narrates itself.** The burner's own output is streamed to the terminal as it happens (prefixed `[burner]`) and to the burner page's log and progress bar at the same time, instead of being held back until the disc was finished - which is what used to make a burn look like it had hung. A progress bar redraws itself in place, so the app renders the stream the way a terminal would (carriage returns rewind the line, backspaces erase, spaces overwrite) rather than splitting it into fragments.
  * **Windows** (IMAPI2) reports one line per track with the percentage of the disc written, the speed that track actually managed, elapsed time and estimated time left, followed by the burn's total time and average speed:
    ```
    [burner] 14 track(s), 61:23 of audio, 619 MB of PCM
    [burner]   [1/14]   7% of disc - 01 - Track.wav - 6.9x, elapsed 00:05:31, eta 01:10:44
    ```
  * **Windows burns the audio, not the WAV files.** IMAPI2 is given raw samples: no header at all, and a stream that is a whole number of CD audio sectors (2352 bytes), because a track that is neither is refused with *the provided audio stream is not valid*. The audio does not even start at a fixed offset - ffmpeg puts a `LIST` chunk holding the track's tags in front of it, so the usual 44 bytes is 78 here. So every track's audio is found by walking the WAV's chunks, written out as raw samples padded with silence to the last whole sector, and the amount of silence that takes is reported (`3 of 14 track(s) are not a whole number of CD sectors, so 0.025s of silence is added to them`). All of that happens **before** the media is prepared, because a disc is spent the moment it is: the app also leaves the disc alone rather than closing it if a burn fails before a single track has been written.
  * **macOS** (`drutil`) is given a pseudo-terminal, because drutil only draws its progress bar when it believes a person is watching - and the terminal is given a real 80x24 window size, since a progress bar drawn onto a zero-width terminal is invisible. Its output arrives live, so the log shows each `Writing track N ...` and the bar's percentage as the disc is written; if it ever goes quiet you still get a `still burning - 0:12:30 elapsed, 0 line(s) from the burner so far` heartbeat every 20 seconds. The command is `drutil burn … <disc folder>/DeckCastDJ.toc` (the layout described above, rather than `-audio <disc folder>`, which would make drutil read the directory itself), and it no longer carries `-eject`: when the burn is done the disc is read back (`drutil trackinfo`) and its report is printed as it arrives - the drive's own answer for what the disc holds, rather than what the log says was sent to it - and only then is the disc ejected.
  * The percentage the burner reports also drives the page's **progress bar**, so percent complete is visible without reading the log.
  * **The bar follows the burn, not the burner's redraws.** drutil draws one bar for the whole disc as it writes, and behind it the bars of the steps it runs around every track - and a step finishing says 100% of itself whatever the disc is doing. Only the disc's own figure is read (the smallest on the line, because a line usually still carries the tail of the one before it), a line announcing a step or a track is ignored, and the figure is never allowed to go backwards or past 99 before the burn ends. Reading those 100% figures as progress is what used to fill the bar during the first track and leave it there while the disc was being written, and a label that only moved when the figure did sat on "Writing track 1" for the rest of the burn. That is what the label says now, with the disc figure next to it - `Writing track 7 - 42% of disc` - instead of the redrawn line.
  * The standalone `python src/cdburn.py burn ...` prints the same stream, so a disc can be written from a shell.
  * The first lines say which capture mode is in use (`burner output: pseudo-terminal (80x24)` on macOS, `pipe` on Windows), and text the burner writes without a line ending is shown within two seconds rather than being held until it finishes a line.
  * `ytd/cd/last_burn.log` records every line with the time it arrived, which is how "the burner said nothing" is told apart from "the app only showed it at the end". It is overwritten by each burn and is a good thing to attach to a bug report.
* **Live progress does not flood the log.** For as long as the burner page is open it asks `/burner/state` for a snapshot every 1-3 seconds so the log, progress bar and drive panel stay current. Those requests are deliberately left out of the access log, so `nohup.out` (and the terminal) only shows real activity. Every other request, and every error, is still logged.
* **Premastered audio is cached** under `ytd/cd/`; the music library and its `audiodb.txt` index stay under `ytd/music/`. Use **Clear CD Cache** on the burner page (or `python src/cdburn.py clean --dry-run` first) to reclaim space. The button clears the whole CD cache, not just the open project, and deletes the now-empty project and disc folders with it - so a finished project never leaves stray directories behind. Add **include sources** to also remove leftover downloads (`ytd/music_src/`, old copies in `ytd/music/`, and the `*_src.*` files YouTube leaves behind). The Track Numbers analysis cache (`ytd/*_11k.wav`) is never touched.
* **The media folder is named after the project.** The name box is pushed to the server as soon as it changes (on blur, and again with **Acquire Audio** or **Save Project**), so WAVs downloaded before the project is saved still land in `ytd/cd/<project>/`. Renaming a project moves that folder with it, which means an acquired disc keeps its audio instead of looking un-downloaded. Renaming is refused while a job is running, since the files would move out from under the downloader.
* **Tracks move with the ▲/▼ buttons**, or **Sort A-Z** for alphabetical order. Track order decides the disc layout and the burn order, and premastered WAVs are renumbered to match - so reordering, sorting, removing a track or switching the blank size never costs a re-download. The first row's ▲ and the last row's ▼ are disabled.
* **Edits follow saved projects.** Once a project has been saved, adding, removing, sorting or moving tracks (and changing the disc size) is written back to the store immediately, so **Load** and a restart bring back exactly what is on screen. A project that has never been saved lives in memory until **Save Project** is pressed.
* **The ▶ button previews a track before you burn it.** Playback starts from the premastered WAV on the server, so only tracks that have already been acquired can be previewed - a track that is not ready yet reports **Nothing to preview there yet - run Acquire Audio first.** Only one track plays at a time, playback stops by itself after 60 seconds, and it stops early if you preview another track, press the same ▶ again (it turns into ■), press **Stop Play** next to **Sort A-Z**, or remove the playing track. The 60 second limit keeps a preview from holding the burner busy.
* **The burner is local-only.** The whole feature - the page and every action in it - is refused for any client that is not on `127.0.0.1`, because the drive is attached to the machine running DeckCastDJ and acquiring audio would let anyone who can reach the page fill that machine's disk. A publicly exposed instance therefore has no burner at all, the DJ/MP3 **Burn to CD** buttons only work on that machine, and a refused client gets an explanatory alert. To use the burner from another machine on a trusted network, add `"burnerAllowRemote": true` to `data/settings.json`, or tunnel the port (`ssh -L 5054:127.0.0.1:5054 user@host`) so the browser still talks to `127.0.0.1`. Note that a request arriving through a reverse proxy looks local, since only the socket's own address is checked.
* Saved CD projects live in `data/burner.json`.

### Command line
The burner also runs standalone:

```bash
python src/cdburn.py plan      --url https://www.youtube.com/playlist?list=...   # show the disc layout
python src/cdburn.py acquire   --project guest@RoadTrip
python src/cdburn.py premaster --project guest@RoadTrip
python src/cdburn.py burn      --project guest@RoadTrip --dry-run
python src/cdburn.py burn      --project guest@RoadTrip --test      # macOS: real burn, disc left blank
python src/cdburn.py progress-demo                                  # what a burn prints, no drive needed
python src/cdburn.py clean     --project guest@RoadTrip --tier wav --dry-run
```

`--test` asks `drutil` for a **simulated burn**: the drive goes through every step, the progress output is identical, and the disc is left blank - so the display can be checked without spending a disc. This is what the page's **Test Burn** button does. Where a test burn is not possible - on Windows, where IMAPI2 can only really write, or when no blank disc is inserted - the burn is taken as far as it goes without the drive instead: the WAVs are parsed, the samples located and the sector padding worked out, or the command that would run is printed. A test never quietly turns into a real burn.

`python src/cdburn.py burn --project <name> --check` (or `-Check` on `ytd/cd/burn_windows.ps1`) runs that audio check on its own on Windows: no drive, no disc, nothing written. It is what the page's **Test Burn** runs there. On macOS `--check` reports the same two things a drutil burn depends on - the folder matching the page's list, and every file holding 44.1 kHz 16-bit stereo PCM in full - and needs neither drive nor disc either.

`progress-demo` needs no drive and no disc at all: it replays a scripted stand-in for `drutil` through the same capture path, using the shapes a real burn puts out (including the bars drutil redraws in place and the per-track step that reports 100% of itself), so what it prints is what a burn prints - and the same lines go to the burner page's log, with the same figures driving its progress bar. It checks itself as it goes: the demo knows where the disc really is, so a bar that runs ahead of the burn or sticks part way through is reported as `FAILED` and the command exits non-zero instead of the problem being found on a disc. Use it after any change to the progress display.

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
