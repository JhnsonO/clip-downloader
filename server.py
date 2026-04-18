#!/usr/bin/env python3
"""
YouTube Clip Trimmer
Runs as a local web server — paste a YouTube URL, set start/end times,
choose quality/aspect/format, download the trimmed clip.
"""

import http.server
import json
import os
import re
import subprocess
import sys
import threading
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

PORT = 8765
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "clipdl_out"

_semaphore = threading.Semaphore(2)

# -- Path resolution ------------------------------------------------------------

def _app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent

def _bundle_dir():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent

APP_DIR    = _app_dir()
BUNDLE_DIR = _bundle_dir()

# -- Binary resolution ----------------------------------------------------------

def _probe(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False

def yt_dlp_cmd():
    for candidate in [
        APP_DIR / ("yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"),
    ]:
        if candidate.exists() and _probe([str(candidate), "--version"]):
            return [str(candidate)]
    if _probe(["yt-dlp", "--version"]):
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]

def ffmpeg_exe():
    bundled = APP_DIR / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if bundled.exists() and _probe([str(bundled), "-version"]):
        return str(bundled)
    if _probe(["ffmpeg", "-version"]):
        return "ffmpeg"
    if sys.platform == "win32":
        for p in [
            Path.home() / "Downloads" / "ffmpeg" / "bin" / "ffmpeg.exe",
            Path("C:/ffmpeg/bin/ffmpeg.exe"),
            Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
            Path.home() / "scoop" / "apps" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe",
        ]:
            if p.exists() and _probe([str(p), "-version"]):
                return str(p)
    return None

# -- Auto-download of binaries (first run only) ---------------------------------

def _download(url, dest, label):
    print(f"  Downloading {label}...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            done  = 0
            with open(dest, "wb") as f:
                while True:
                    buf = resp.read(65536)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    if total:
                        print(f"\r  {done*100//total}%  ", end="", flush=True)
        print(f"\r  [OK] {label} ready ({done//1024} KB)          ")
        return True
    except Exception as e:
        print(f"\n  [ERROR] {label} download failed: {e}")
        return False

def ensure_ytdlp():
    if _probe(yt_dlp_cmd() + ["--version"]):
        return True
    if sys.platform == "win32":
        url  = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
        dest = APP_DIR / "yt-dlp.exe"
    elif sys.platform == "darwin":
        url  = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
        dest = APP_DIR / "yt-dlp"
    else:
        url  = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
        dest = APP_DIR / "yt-dlp"
    ok = _download(url, str(dest), "yt-dlp")
    if ok and sys.platform != "win32":
        os.chmod(dest, 0o755)
    return ok and _probe([str(dest), "--version"])

def ensure_ffmpeg():
    if ffmpeg_exe():
        return True
    if sys.platform != "win32":
        print("  [WARN]  ffmpeg not found.  Mac: brew install ffmpeg  |  Linux: sudo apt install ffmpeg")
        return False
    zip_url  = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    zip_path = APP_DIR / "_ffmpeg_dl.zip"
    print("  ffmpeg not found - downloading (~60 MB, one-time)...")
    if not _download(zip_url, str(zip_path), "ffmpeg"):
        return False
    print("  Extracting ffmpeg.exe...")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                if re.search(r"bin/ffmpeg\.exe$", member):
                    dest = APP_DIR / "ffmpeg.exe"
                    dest.write_bytes(zf.read(member))
                    print(f"  [OK] ffmpeg.exe ready")
                    return True
        print("  [ERROR] ffmpeg.exe not found in zip")
    except Exception as e:
        print(f"  [ERROR] Extraction failed: {e}")
    finally:
        try: zip_path.unlink()
        except: pass
    return False

# -- Video info -----------------------------------------------------------------

def get_video_info(url):
    r = subprocess.run(
        yt_dlp_cmd() + ["--dump-json", "--no-playlist", url],
        capture_output=True, text=True, timeout=90,
        encoding="utf-8", errors="replace"
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "yt-dlp failed")
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError("No metadata returned")

# -- Quality & aspect ratio -----------------------------------------------------

_BITRATES = {
    2160: "8000k",
    1080: "4000k",
    720:  "2500k",
    480:  "1500k",
}

def _quality_fmt(quality):
    q = str(quality)
    return f"bestvideo[height<={q}]+bestaudio[ext=m4a]/bestvideo[height<={q}]+bestaudio/best"

ASPECT_FILTERS = {
    "16:9": "scale='if(gt(a,16/9),ih*16/9,iw)':'if(gt(a,16/9),ih,iw*9/16)',pad='ceil(iw/2)*2':'ceil(ih/2)*2'",
    "9:16": "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale='ceil(iw/2)*2':'ceil(ih/2)*2'",
    "1:1":  "crop='min(iw,ih)':'min(iw,ih)',scale='ceil(iw/2)*2':'ceil(ih/2)*2'",
    "4:3":  "crop='min(iw,ih*4/3)':'min(ih,iw*3/4)',scale='ceil(iw/2)*2':'ceil(ih/2)*2'",
}

def _encode_h264(ff, input_path, output_path, quality="1080", start=None, duration=None, aspect=None):
    height = int(quality) if quality else 1080
    if height <= 720:
        profile, level = "main", "3.1"
    elif height <= 1080:
        profile, level = "high", "4.0"
    else:
        profile, level = "high", "5.1"
    bitrate = _BITRATES.get(height, "4000k")

    cmd = [ff, "-y"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", input_path]
    if duration is not None:
        cmd += ["-t", str(duration)]

    # Build video filter chain
    vf_parts = [f"scale=-2:min({height}\\,ih)"]
    if aspect and aspect != "original" and aspect in ASPECT_FILTERS:
        vf_parts.append(ASPECT_FILTERS[aspect])
    vf = ",".join(vf_parts)

    cmd += [
        "-c:v", "libx264",
        "-profile:v", profile,
        "-level:v", level,
        "-b:v", bitrate,
        "-maxrate", bitrate,
        "-bufsize", str(int(bitrate.replace("k","")) * 2) + "k",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-vf", vf,
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    print(f"[ffmpeg] encoding at {bitrate} bitrate, aspect={aspect}")
    return subprocess.run(cmd, capture_output=True, text=True)


# -- Download + trim ------------------------------------------------------------

def download_clip(url, output_dir, status_path, log_path, start_time, end_time,
                  quality="1080", aspect="original", fmt="mp4", custom_name=None):
    quality = str(quality).strip() if quality else "1080"
    aspect = (aspect or "original").strip().lower()
    fmt = (fmt or "mp4").strip().lower()
    output_dir  = Path(output_dir)
    status_path = Path(status_path)
    log_path    = Path(log_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    def log(msg):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

    try:
        status_path.write_text("fetching_info")
        log("Fetching video info...")
        info = get_video_info(url)

        video_title = (info.get("title") or "clip").strip()
        if custom_name and custom_name.strip():
            video_title = custom_name.strip()
        safe_name = re.sub(r'[\\/*?:"<>|]', "_", video_title).strip("._") or "clip"
        safe_name = f"{safe_name}_{int(start_time)}s-{int(end_time)}s"

        duration = end_time - start_time
        log(f"=== JOB START ===")
        log(f"URL      : {url}")
        log(f"Quality  : {quality}p")
        log(f"Aspect   : {aspect}")
        log(f"Format   : {fmt}")
        log(f"Trim     : {start_time}s -> {end_time}s ({duration:.1f}s)")

        section_arg = f"*{start_time}-{end_time}"

        # ── MP3 (audio only) ──────────────────────────────────────────────
        if fmt == "mp3":
            status_path.write_text("downloading")
            log("Downloading audio segment...")
            out_path = output_dir / f"{safe_name}.mp3"
            r = subprocess.run(
                yt_dlp_cmd() + [
                    "--no-playlist",
                    "-f", "bestaudio/best",
                    "--download-sections", section_arg,
                    "--force-keyframes-at-cuts",
                    "-x", "--audio-format", "mp3",
                    "--audio-quality", "192K",
                    "-o", str(out_path), url
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=600
            )
            log(r.stdout[-1000:] if r.stdout else "")
            if r.returncode != 0:
                log(r.stderr[-500:] if r.stderr else "")
                status_path.write_text("error")
                return
            candidates = list(output_dir.glob(f"{safe_name}*"))
            if candidates:
                final = candidates[0]
                log(f"Done! {final.name}")
                status_path.write_text(f"done:{final.name}:{final}")
            else:
                status_path.write_text("error")
            return

        # ── MP4 (video) ───────────────────────────────────────────────────
        status_path.write_text("downloading")
        log("Downloading video segment...")

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_out = os.path.join(tmpdir, "raw.%(ext)s")
            proc = subprocess.Popen(
                yt_dlp_cmd() + [
                    "--no-playlist",
                    "-f", _quality_fmt(quality),
                    "--download-sections", section_arg,
                    "--force-keyframes-at-cuts",
                    "--merge-output-format", "mp4",
                    "--newline",
                    "-o", raw_out, url
                ],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, encoding="utf-8", errors="replace"
            )
            for line in proc.stdout:
                log(line.rstrip())
            proc.wait()

            if proc.returncode != 0:
                log("Download failed.")
                status_path.write_text("error")
                return

            files = list(Path(tmpdir).glob("raw.*"))
            if not files:
                log("File not found.")
                status_path.write_text("error")
                return
            raw_path = str(files[0])

            # ── Encode / aspect ratio ─────────────────────────────────────
            out_path = output_dir / f"{safe_name}.mp4"
            ff = ffmpeg_exe()
            if not ff:
                import shutil
                shutil.copy2(raw_path, str(out_path))
                log("No ffmpeg — raw copy saved.")
                status_path.write_text(f"done:{out_path.name}:{out_path}")
                return

            status_path.write_text("trimming")

            # Log source info
            probe = subprocess.run([ff, "-i", raw_path],
                                   capture_output=True, text=True)
            log("=== SOURCE ===")
            for line in (probe.stderr + probe.stdout).splitlines():
                if any(k in line.lower() for k in ["video:", "audio:", "stream", "duration"]):
                    log(f"  {line.strip()}")

            needs_encode = (aspect and aspect != "original") or True
            log(f"=== ENCODING ===")
            r1 = _encode_h264(ff, raw_path, str(out_path), quality,
                              start=None, duration=None, aspect=aspect)
            log(f"ffmpeg exit: {r1.returncode}")
            if r1.stderr:
                log(f"stderr: {r1.stderr[-800:]}")
            if r1.returncode != 0:
                status_path.write_text("error")
                return

            if out_path.exists():
                size_mb = out_path.stat().st_size / 1024 / 1024
                log(f"=== OUTPUT ===")
                log(f"Size: {size_mb:.1f} MB")

        log(f"Saved: {out_path}")
        status_path.write_text(f"done:{out_path.name}:{out_path}")

    except subprocess.TimeoutExpired:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\nTimed out — video may be too long\n")
        status_path.write_text("error")
    except Exception as e:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\nFATAL: {e}\n")
        status_path.write_text("error")

# -- HTTP handler ---------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, *_): pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")

        elif path == "/manifest.json":
            mf = (BUNDLE_DIR / "manifest.json")
            data = mf.read_bytes() if mf.exists() else b'{}'
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        elif path == "/icon.png":
            svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"><rect width="192" height="192" rx="40" fill="#0a0a0a"/><text y="130" x="96" font-size="100" text-anchor="middle" fill="#c8f135">&#11015;</text></svg>'
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(svg)))
            self.end_headers()
            self.wfile.write(svg)

        elif path in ("/", "/index.html"):
            html = (BUNDLE_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        elif path == "/api/storyboard-proxy":
            qs = urllib.parse.urlparse(self.path).query
            sb_url = urllib.parse.parse_qs(qs).get("url", [""])[0]
            if not sb_url:
                self.send_json({"error": "No url param"}, 400); return
            try:
                req = urllib.request.Request(sb_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    img_data = resp.read()
                    ct = resp.headers.get("Content-Type", "image/jpeg")
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(img_data)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(img_data)
            except Exception as e:
                self.send_json({"error": str(e)}, 502)

        elif path == "/api/status":
            cmd = yt_dlp_cmd()
            yt_ok  = _probe(cmd + ["--version"])
            yt_ver = ""
            if yt_ok:
                r = subprocess.run(cmd + ["--version"], capture_output=True, text=True)
                yt_ver = r.stdout.strip().splitlines()[0]
            ff     = ffmpeg_exe()
            ff_ok  = ff is not None
            ff_ver = ""
            if ff_ok:
                r = subprocess.run([ff, "-version"], capture_output=True, text=True)
                ff_ver = r.stdout.splitlines()[0] if r.stdout else ""
            self.send_json({
                "yt_dlp":      {"installed": yt_ok, "version": yt_ver},
                "ffmpeg":      {"installed": ff_ok, "version": ff_ver, "path": ff or ""},
                "download_dir": str(DOWNLOAD_DIR),
            })

        elif path.startswith("/api/file/"):
            job_id = path.split("/api/file/")[1]
            tmp    = tempfile.gettempdir()
            sp     = Path(os.path.join(tmp, f"ytclip_{job_id}.status"))
            status = sp.read_text(encoding="utf-8").strip() if sp.exists() else ""
            fpath  = None
            fname  = "clip.mp4"
            if status.startswith("done:"):
                parts = status.split(":", 2)
                fname = parts[1] if len(parts) > 1 else fname
                fpath = parts[2] if len(parts) > 2 else None
            if not fpath or not Path(fpath).exists():
                self.send_json({"error": "File not ready"}, 404); return
            fsize = Path(fpath).stat().st_size
            safe  = fname.encode("ascii", "ignore").decode() or "clip.mp4"
            mime  = "audio/mpeg" if safe.endswith(".mp3") else "video/mp4"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(fsize))
            self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with open(fpath, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except Exception:
                        break
            try:
                Path(fpath).unlink(missing_ok=True)
            except Exception:
                pass

        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
        except Exception:
            self.send_json({"error": "Bad JSON"}, 400); return

        if self.path == "/api/info":
            url = data.get("url", "").strip()
            if not url:
                self.send_json({"error": "No URL"}, 400); return
            try:
                info = get_video_info(url)
                # Extract storyboard/thumbnail sprite sheets
                storyboards = []
                for t in (info.get("thumbnails") or []):
                    tid = t.get("id", "")
                    # yt-dlp storyboard entries have IDs like "sb0", "sb1", "sb2"
                    if tid.startswith("sb"):
                        storyboards.append({
                            "url":    t.get("url", ""),
                            "width":  t.get("width"),
                            "height": t.get("height"),
                            "cols":   t.get("columns"),
                            "rows":   t.get("rows"),
                            "count":  t.get("n_frames") or t.get("frame_count"),
                        })
                self.send_json({
                    "ok":          True,
                    "title":       info.get("fulltitle") or info.get("title", ""),
                    "channel":     info.get("uploader", ""),
                    "duration":    info.get("duration"),
                    "thumbnail":   info.get("thumbnail", ""),
                    "storyboards": storyboards,
                })
            except Exception as e:
                self.send_json({"error": str(e)}, 400)

        elif self.path == "/api/download":
            url        = data.get("url", "").strip()
            job_id     = data.get("id", "job")
            start_time = data.get("start", 0)
            end_time   = data.get("end", 10)
            quality    = data.get("quality", "1080")
            aspect     = data.get("aspect", "original")
            fmt        = data.get("format", "mp4")
            filename   = data.get("filename", "").strip()

            if not url:
                self.send_json({"error": "No URL"}, 400); return

            try:
                start_time = float(start_time)
                end_time   = float(end_time)
            except (TypeError, ValueError):
                self.send_json({"error": "Invalid start/end time"}, 400); return

            if end_time <= start_time:
                self.send_json({"error": "End time must be after start time"}, 400); return
            if (end_time - start_time) > 600:
                self.send_json({"error": "Max trim duration is 10 minutes"}, 400); return

            tmp = tempfile.gettempdir()
            sp  = os.path.join(tmp, f"ytclip_{job_id}.status")
            lp  = os.path.join(tmp, f"ytclip_{job_id}.log")
            Path(sp).write_text("starting")
            Path(lp).write_text("")

            def _run(job_id, url, sp, lp, start_time, end_time, quality, aspect, fmt, filename):
                Path(sp).write_text("queued")
                with open(lp, "a", encoding="utf-8") as f:
                    f.write("Queued - waiting for available slot...\n")
                with _semaphore:
                    with open(lp, "a", encoding="utf-8") as f:
                        f.write("Slot acquired - starting download...\n")
                    download_clip(url, str(DOWNLOAD_DIR), sp, lp,
                                  start_time, end_time,
                                  quality=quality, aspect=aspect, fmt=fmt,
                                  custom_name=filename or None)

            threading.Thread(
                target=_run,
                args=(job_id, url, sp, lp, start_time, end_time,
                      quality, aspect, fmt, filename),
                daemon=True
            ).start()
            self.send_json({"ok": True, "id": job_id})

        elif self.path == "/api/poll":
            job_id = data.get("id", "job")
            tmp    = tempfile.gettempdir()
            sp     = Path(os.path.join(tmp, f"ytclip_{job_id}.status"))
            lp     = Path(os.path.join(tmp, f"ytclip_{job_id}.log"))
            status = sp.read_text(encoding="utf-8").strip() if sp.exists() else "unknown"
            tail   = lp.read_text(encoding="utf-8")[-5000:] if lp.exists() else ""
            fname  = None
            fpath  = None
            if status.startswith("done:"):
                parts  = status.split(":", 2)
                fname  = parts[1] if len(parts) > 1 else None
                fpath  = parts[2] if len(parts) > 2 else None
                status = "done"
            self.send_json({"status": status, "log": tail, "filename": fname, "filepath": fpath})

        else:
            self.send_json({"error": "Not found"}, 404)

# -- Entry point ----------------------------------------------------------------

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 40)
    print("  YouTube Clip Trimmer")
    print(f"  http://localhost:{PORT}")
    print("  Files stream to device, nothing saved locally")
    print("=" * 40)
    print()
    print("Checking tools (downloading if needed)...")

    if not ensure_ytdlp():
        print("[ERROR] yt-dlp unavailable - check internet connection.")
    else:
        cmd = yt_dlp_cmd()
        r = subprocess.run(cmd + ["--version"], capture_output=True, text=True)
        print(f"[OK] yt-dlp {r.stdout.strip()}")

    if not ensure_ffmpeg():
        print("[WARN]  No ffmpeg - clips won't be re-encoded.")
    else:
        print(f"[OK] ffmpeg @ {ffmpeg_exe()}")

    print()
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print("Running. Close this window to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
