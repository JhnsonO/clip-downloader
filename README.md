# Clip Trimmer

Paste a YouTube URL, set start/end times, pick quality & aspect ratio, download the trimmed clip.

---

## Daily use

1. Double-click **START.bat**
2. A URL appears in the window — send it to Kris and Wade
3. They open it on their phone, paste a YouTube link, set the trim points
4. Pick quality (1080p/720p/etc), aspect ratio (Original/9:16 for Shorts/1:1), and format (MP4 or MP3)
5. Hit **Trim & Download** — the file saves directly to their device
6. Close the window when done

---

## What changed from Clip Downloader

- **No longer clip-URL only** — works with any YouTube video URL
- **Visual trimmer** — embedded player + dual-handle timeline slider to set start/end times
- **Aspect ratio** — crop to 9:16 (Shorts), 1:1 (Square), 4:3, or keep original
- **MP3 export** — extract audio only
- **Max 10 min clips** — keeps downloads fast

---

## Getting a permanent URL (recommended — do this once)

Without setup, you get a random URL each time. To get a fixed URL that never changes:

1. Go to https://ngrok.com and create a free account
2. Download **ngrok** for Windows from https://ngrok.com/download
3. Extract **ngrok.exe** and put it in this folder (next to START.bat)
4. In a terminal, run:
   ```
   ngrok config add-authtoken YOUR_TOKEN_HERE
   ```
   (your token is on the ngrok dashboard after signing in)
5. In the ngrok dashboard, go to **Cloud Edge > Domains** and grab your free domain
   e.g. `yourname.ngrok-free.app`
6. Edit START.bat and replace the domain with yours

From then on, Kris and Wade always use the same URL — bookmark it on their phones.

---

## Requirements

- Python 3 (python.org) — install once, tick "Add to PATH"
- Everything else downloads automatically on first run
