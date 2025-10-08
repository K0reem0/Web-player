"""
Flask video downloader for Heroku — waits for full download before playing, with progress bar.

Usage:
- Paste a direct video URL.
- Server downloads the video fully (showing progress bar).
- Once done, automatically redirects to video player with seeking support.

Requires:
Flask>=2.0
requests>=2.28
gunicorn

Procfile:
web: gunicorn heroku_video_server:app
"""

import os
import re
import json
import hashlib
import threading
from urllib.parse import urlparse
from flask import Flask, request, redirect, url_for, render_template_string, send_file, abort, jsonify
import requests

APP_ROOT = os.path.dirname(__file__)
VIDEO_DIR = os.path.join(APP_ROOT, "videos")
INDEX_PATH = os.path.join(VIDEO_DIR, "index.json")
os.makedirs(VIDEO_DIR, exist_ok=True)

# Load or initialize video index
if os.path.exists(INDEX_PATH):
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            INDEX = json.load(f)
    except Exception:
        INDEX = {}
else:
    INDEX = {}

app = Flask(__name__)


def save_index():
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(INDEX, f, ensure_ascii=False, indent=2)


def safe_filename(url):
    name = os.path.basename(urlparse(url).path) or "video.mp4"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    h = hashlib.sha1(url.encode()).hexdigest()[:8]
    return f"{h}_{name}"


def download_file(url, dest_path, key):
    try:
        with requests.get(url, stream=True, timeout=20) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    INDEX[key]["progress"] = int(downloaded / total * 100) if total else 0
                    save_index()
        INDEX[key]["saved"] = True
        INDEX[key]["path"] = os.path.basename(dest_path)
        save_index()
    except Exception as e:
        print("Download failed:", e)
        INDEX.pop(key, None)
        save_index()
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except:
            pass


@app.route("/")
def home():
    return render_template_string(
        """
    <h2>تحميل فيديو</h2>
    <form action="/add" method="post">
      <input name="url" placeholder="رابط مباشر للفيديو" style="width:70%" required>
      <button type="submit">تحميل</button>
    </form>
    <ul>
    {% for k,v in videos.items() %}
      <li>
        {{v.title}} — 
        {% if v.saved %}
          <a href="/player/{{k}}">مشاهدة</a>
        {% else %}
          {{v.progress or 0}}%
        {% endif %}
        <form action="/delete/{{k}}" method="post" style="display:inline">
          <button>حذف</button>
        </form>
      </li>
    {% else %}
      <li>لا يوجد فيديوهات</li>
    {% endfor %}
    </ul>
    """,
        videos=INDEX,
    )


@app.route("/add", methods=["POST"])
def add():
    url = request.form.get("url")
    if not url:
        return redirect(url_for("home"))
    key = hashlib.sha1(url.encode()).hexdigest()
    if key in INDEX:
        return redirect(url_for("progress", key=key))
    name = safe_filename(url)
    INDEX[key] = {"url": url, "title": name, "progress": 0, "saved": False}
    save_index()
    path = os.path.join(VIDEO_DIR, name)
    threading.Thread(target=download_file, args=(url, path, key), daemon=True).start()
    return redirect(url_for("progress", key=key))


@app.route("/progress/<key>")
def progress(key):
    meta = INDEX.get(key)
    if not meta:
        abort(404)
    return render_template_string(
        """
    <h3>جاري تحميل الفيديو...</h3>
    <div style="width:80%;height:25px;border:1px solid #ccc;border-radius:10px;overflow:hidden">
      <div id="bar" style="height:100%;width:{{meta.progress}}%;background:#3498db"></div>
    </div>
    <p id="percent">{{meta.progress}}%</p>
    <script>
    async function update(){
      let res = await fetch('/progress_data/{{key}}');
      let data = await res.json();
      document.getElementById('bar').style.width = data.progress+'%';
      document.getElementById('percent').textContent = data.progress+'%';
      if(data.saved){window.location='/player/{{key}}';}
      else setTimeout(update,2000);
    }
    setTimeout(update,2000);
    </script>
    """,
        key=key,
        meta=meta,
    )


@app.route("/progress_data/<key>")
def progress_data(key):
    meta = INDEX.get(key)
    if not meta:
        return jsonify({"error": "not found"}), 404
    return jsonify({"progress": meta.get("progress", 0), "saved": meta.get("saved", False)})


@app.route("/player/<key>")
def player(key):
    meta = INDEX.get(key)
    if not meta or not meta.get("saved"):
        abort(404)
    path = meta["path"]
    return render_template_string(
        """
    <h3>{{meta.title}}</h3>
    <video controls autoplay style="width:100%;max-width:900px">
      <source src="/videos/{{path}}" type="video/mp4">
    </video>
    """,
        meta=meta,
        path=path,
    )


@app.route("/videos/<path:filename>")
def serve_video(filename):
    file_path = os.path.join(VIDEO_DIR, os.path.basename(filename))
    if not os.path.exists(file_path):
        abort(404)
    return send_file(file_path)


@app.route("/delete/<key>", methods=["POST"])
def delete(key):
    meta = INDEX.get(key)
    if not meta:
        return ("", 404)
    if meta.get("path"):
        fp = os.path.join(VIDEO_DIR, os.path.basename(meta["path"]))
        if os.path.exists(fp):
            os.remove(fp)
    INDEX.pop(key, None)
    save_index()
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
