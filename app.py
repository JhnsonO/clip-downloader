import os
import re
import json
import uuid
import subprocess
import threading
import tempfile
import shutil
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template

app = Flask(__name__)

JOBS = {}
JOBS_LOCK = threading.Lock()

TMP = Path(tempfile.gettempdir()) / "clipdl"
TMP.mkdir(exist_ok=True)

# ── Cookies setup ──────────────────────────────────────────────────────────────
COOKIES_FILE = TMP / "cookies.txt"

def setup_cookies():
    cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if cookies:
        COOKIES_FILE.write_text(cookies)
        print(f"[cookies] Written {len(cookies)} bytes")
    else:
        print("[cookies] No YOUTUBE_COOKIES env var — may hit bot detection")

setup_cookies()


def extra_args():
    """Extra args for every yt-dlp call: JS runtime + cookies."""
    args = ["--js-runtimes", "node"]
    if COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0:
        args += ["--cookies", str(COOKIES_FILE)]
    return args


# ── yt-dlp ─────────────────────────────────────────────────────────────────────

def yt_dlp():
    for candidate in ["yt-dlp", "yt_dlp"]:
        try:
            r = subprocess.run([candidate, "--version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return candidate
        except FileNotFoundError:
            pass
    return None


def extract_clip_title(info):
    clip_obj = info.get("clip")
    if isinstance(clip_obj, dict):
        t = clip_obj.get("title") or clip_obj.get("name")
        if t:
            return t.strip()
    for key in ("clip_title", "clip_name"):
        t = info.get(key)
        if t:
            return t.strip()
    title     = (info.get("title") or "").strip()
    fulltitle = (info.get("fulltitle") or "").strip()
    if fulltitle and title and title != fulltitle:
        return title
    return title or "clip"


def get_clip_info(url):
    ytdlp = yt_dlp()
    if not ytdlp:
        raise RuntimeError("yt-dlp not available on server")
    r = subprocess.run(
        [ytdlp, "--dump-json", "--no-playlist"] + extra_args() + [url],
        capture_output=True, text=True, timeout=90,
        encoding="utf-8", errors="replace"
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "yt-dlp failed")
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError("No metadata returned by yt-dlp")


# ── Download job ───────────────────────────────────────────────────────────────

def run_download(job_id, url):
    job_dir = TMP / job_id
    job_dir.mkdir(exist_ok=True)

    def set_status(s, **kw):
        with JOBS_LOCK:
            JOBS[job_id].update({"status": s, **kw})

    def log(msg):
        with JOBS_LOCK:
            JOBS[job_id]["log"] = JOBS[job_id].get("log", "") + msg + "\n"

    try:
        set_status("fetching_info")
        log("Fetching clip info...")
        info = get_clip_info(url)

        clip_title = extract_clip_title(info)
        safe_name  = re.sub(r'[\\/*?:"<>|]', "_", clip_title).strip("._") or "clip"
        start_time = info.get("start_time") or info.get("clip_start_time")
        end_time   = info.get("end_time")   or info.get("clip_end_time")

        log(f"Clip: {clip_title}")
        log(f"Timestamps: {start_time}s → {end_time}s")

        ytdlp = yt_dlp()
        ea = extra_args()

        if start_time is None or end_time is None:
            log("No timestamps — downloading full resolution.")
            set_status("downloading")
            out = job_dir / f"{safe_name}.mp4"
            r = subprocess.run(
                [ytdlp, "--no-playlist", "-f", "bestvideo+bestaudio/best",
                 "--merge-output-format", "mp4"] + ea + ["-o", str(out), url],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=600
            )
            log(r.stdout[-1000:])
            if r.returncode != 0:
                log(r.stderr[-500:])
                set_status("error", error="Download failed")
                return
            set_status("done", filename=out.name, path=str(out))
            return

        set_status("downloading")
        log("Downloading full video...")
        tmp_video = job_dir / "full.%(ext)s"
        r = subprocess.run(
            [ytdlp, "--no-playlist", "-f", "bestvideo+bestaudio/best",
             "--merge-output-format", "mkv"] + ea + ["-o", str(tmp_video), url],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600
        )
        log(r.stdout[-1000:])
        if r.returncode != 0:
            log(r.stderr[-500:])
            set_status("error", error="Download failed")
            return

        files = list(job_dir.glob("full.*"))
        if not files:
            set_status("error", error="Downloaded file missing")
            return
        full_path = str(files[0])

        set_status("trimming")
        duration = end_time - start_time
        out = job_dir / f"{safe_name}.mp4"
        log(f"Trimming {duration:.1f}s clip...")

        r1 = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start_time), "-i", full_path,
             "-t", str(duration), "-c", "copy", str(out)],
            capture_output=True, text=True
        )
        if r1.returncode != 0:
            log("Stream copy failed — re-encoding...")
            r2 = subprocess.run(
                ["ffmpeg", "-y", "-ss", str(start_time), "-i", full_path,
                 "-t", str(duration), "-c:v", "libx264", "-crf", "18",
                 "-preset", "fast", "-c:a", "aac", "-b:a", "192k", str(out)],
                capture_output=True, text=True
            )
            if r2.returncode != 0:
                log(r2.stderr[-500:])
                set_status("error", error="Trim failed")
                return

        log("Done!")
        set_status("done", filename=out.name, path=str(out))

    except subprocess.TimeoutExpired:
        set_status("error", error="Timed out — video may be too long")
    except Exception as e:
        set_status("error", error=str(e))


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return "ok", 200


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def api_info():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL"}), 400
    try:
        info  = get_clip_info(url)
        start = info.get("start_time") or info.get("clip_start_time")
        end   = info.get("end_time")   or info.get("clip_end_time")
        dur   = round(end - start, 1) if (start is not None and end is not None) else None
        return jsonify({
            "ok":            True,
            "title":         extract_clip_title(info),
            "video_title":   info.get("fulltitle") or info.get("title", ""),
            "channel":       info.get("uploader", ""),
            "thumbnail":     info.get("thumbnail", ""),
            "clip_duration": dur,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
def api_download():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL"}), 400
    job_id = str(uuid.uuid4())[:8]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "starting", "log": ""}
    threading.Thread(target=run_download, args=(job_id, url), daemon=True).start()
    return jsonify({"ok": True, "id": job_id})


@app.route("/api/poll/<job_id>")
def api_poll(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id, {}).copy()
    if not job:
        return jsonify({"status": "unknown"})
    return jsonify({
        "status":   job.get("status"),
        "log":      job.get("log", "")[-1500:],
        "filename": job.get("filename"),
        "error":    job.get("error"),
    })


@app.route("/api/file/<job_id>")
def api_file(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id, {})
    path = job.get("path")
    name = job.get("filename", "clip.mp4")
    if not path or not Path(path).exists():
        return jsonify({"error": "File not found"}), 404
    def cleanup():
        try:
            shutil.rmtree(str(TMP / job_id), ignore_errors=True)
        except Exception:
            pass
    response = send_file(path, as_attachment=True, download_name=name, mimetype="video/mp4")
    threading.Timer(30, cleanup).start()
    return response


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
