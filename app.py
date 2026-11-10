import os
import re
import uuid
import asyncio
import subprocess

from flask import Flask, request, send_file, jsonify, Response
from playwright.sync_api import sync_playwright
import edge_tts

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024

OUT_DIR = "/tmp/code2video"
os.makedirs(OUT_DIR, exist_ok=True)

FPS = 24
MAX_SCENES = 8
MAX_SCENE_SEC = 12
MAX_VOICED_SEC = 30
MAX_TOTAL_SEC = 60

FORMATS = {
    "youtube": (1920, 1080),
    "shorts": (1080, 1920),
    "square": (1080, 1080),
}

VOICES = {
    "uz_female": "uz-UZ-MadinaNeural",
    "uz_male": "uz-UZ-SardorNeural",
}

IMG_RE = re.compile(r"data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+")

IMG_TPL = """<!DOCTYPE html>
<html><head><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#000}
img{width:100%;height:100%;object-fit:cover;animation:kb 6s ease-in-out forwards}
@keyframes kb{from{transform:scale(1)}to{transform:scale(1.15)}}
</style></head><body><img src="__SRC__"></body></html>"""

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CODE2VIDEO AI</title>
<style>
  :root { --bg:#0b0d14; --card:#141824; --txt:#e8eaf2; --a1:#8b5cf6; --a2:#3b82f6; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, sans-serif; background:var(--bg); color:var(--txt); padding:24px; }
  h1 { margin:0 0 4px; background:linear-gradient(90deg,var(--a1),var(--a2)); -webkit-background-clip:text; color:transparent; }
  .sub { color:#8a90a6; margin-bottom:20px; }
  .card { background:var(--card); border-radius:16px; padding:20px; max-width:860px; margin:auto; }
  label { display:block; margin:14px 0 6px; font-size:14px; color:#aab; }
  textarea, select, input { width:100%; background:#0e1220; color:var(--txt); border:1px solid #262c40; border-radius:10px; padding:10px; font-size:14px; }
  textarea { min-height:140px; font-family: monospace; }
  .row { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; }
  .btns { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:16px; }
  button { padding:14px; border:0; border-radius:12px; font-size:15px; font-weight:600; color:#fff; background:linear-gradient(90deg,var(--a1),var(--a2)); cursor:pointer; }
  button.alt { background:#262c40; }
  button:disabled { opacity:.5; cursor:wait; }
  #list { margin-top:12px; color:#8a90a6; font-size:14px; }
  #status { margin-top:14px; color:#8a90a6; min-height:20px; }
  #dl { display:none; margin-top:12px; padding:12px 20px; border-radius:10px; background:#10b981; color:#fff; text-decoration:none; font-weight:600; text-align:center; }
  .hint { font-size:12px; color:#6b7189; margin-top:6px; }
  @media (max-width:600px) { .row, .btns { grid-template-columns:1fr; } }
</style>
</head>
<body>
<div class="card">
  <h1>CODE2VIDEO AI</h1>
  <div class="sub">HTML/CSS animatsiya yoki rasmdan ovozli MP4 video yarating</div>

  <div class="row">
    <div>
      <label>Format</label>
      <select id="format">
        <option value="youtube">YouTube 16:9</option>
        <option value="shorts">Shorts / TikTok 9:16</option>
        <option value="square">Instagram 1:1</option>
      </select>
    </div>
    <div>
      <label>Sahna davomiyligi (soniya)</label>
      <input id="dur" type="number" value="4" min="1" max="12" step="0.5">
    </div>
    <div>
      <label>Ovoz balandligi</label>
      <input id="vol" type="number" value="1" min="0.2" max="2" step="0.1">
    </div>
  </div>

  <label>HTML kod (sahna uchun)</label>
  <textarea id="html" placeholder="<div>...</div><style>...</style>"></textarea>

  <label>Yoki rasm (rasm tanlansa HTML e'tiborga olinmaydi)</label>
  <input id="img" type="file" accept="image/png,image/jpeg,image/webp,image/gif">
  <div class="hint" id="imgname"></div>

  <label>Dialog / matn (har qatorga bitta)</label>
  <textarea id="lines" style="min-height:100px" placeholder="f: Assalomu alaykum&#10;m: Va alaykum assalom&#10;Oddiy matn ayol ovozida o'qiladi"></textarea>
  <div class="hint">"f:" ayol ovozi, "m:" erkak ovozi. Prefiksiz qatorlar ayol ovozida o'qiladi.</div>

  <div class="btns">
    <button id="add" class="alt">Sahna qo'shish</button>
    <button id="clear" class="alt">Sahnalarni tozalash</button>
  </div>
  <div id="list">Sahnalar: 0 ta</div>

  <button id="go" style="width:100%;margin-top:16px">Video yaratish</button>
  <div id="status"></div>
  <a id="dl" download="code2video.mp4">MP4 yuklab olish</a>
</div>

<script>
const $ = id => document.getElementById(id);
let scenes = [];
let imgData = null;

$("img").onchange = e => {
  const f = e.target.files[0];
  if (!f) return;
  const r = new FileReader();
  r.onload = () => { imgData = r.result; $("imgname").textContent = "Tanlangan rasm: " + f.name; };
  r.readAsDataURL(f);
};

function parseLines(text) {
  return text.split("\n").map(s => s.trim()).filter(Boolean).map(s => {
    const m = s.match(/^(f|m):\s*(.*)$/i);
    if (m) return { voice: m[1].toLowerCase() === "m" ? "uz_male" : "uz_female", text: m[2] };
    return { voice: "uz_female", text: s };
  });
}

function refreshList() {
  $("list").textContent = "Sahnalar: " + scenes.length + " ta";
}

$("add").onclick = () => {
  const sc = { duration: Number($("dur").value) || 4, lines: parseLines($("lines").value) };
  if (imgData) {
    sc.image = imgData;
  } else if ($("html").value.trim()) {
    sc.html = $("html").value;
  } else {
    alert("HTML kod yoki rasm kiriting");
    return;
  }
  scenes.push(sc);
  imgData = null;
  $("img").value = "";
  $("imgname").textContent = "";
  $("html").value = "";
  $("lines").value = "";
  refreshList();
};

$("clear").onclick = () => { scenes = []; refreshList(); };

$("go").onclick = async () => {
  if (!scenes.length) { alert("Avval kamida bitta sahna qo'shing"); return; }
  $("go").disabled = true;
  $("dl").style.display = "none";
  $("status").textContent = "Video yaratilmoqda, bir necha daqiqa kutishingiz mumkin...";
  try {
    const r = await fetch("/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        format: $("format").value,
        volume: Number($("vol").value) || 1,
        scenes: scenes
      })
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.error || "Noma'lum xatolik");
    }
    const blob = await r.blob();
    $("dl").href = URL.createObjectURL(blob);
    $("dl").style.display = "block";
    $("status").textContent = "Tayyor!";
  } catch (e) {
    $("status").textContent = "Xato: " + e.message;
  }
  $("go").disabled = false;
};
</script>
</body>
</html>"""


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def probe_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


async def _tts(text, voice, path):
    await edge_tts.Communicate(text, voice).save(path)


def tts(text, voice, path):
    asyncio.run(_tts(text, voice, path))


def normalize(data):
    raw = data.get("scenes") or []
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SCENES:
        raise ValueError(f"1 dan {MAX_SCENES} gacha sahna kiriting")

    scenes = []
    for n, sc in enumerate(raw, 1):
        if not isinstance(sc, dict):
            raise ValueError(f"{n}-sahna noto'g'ri formatda")

        image = (sc.get("image") or "").strip()
        if image:
            if not IMG_RE.fullmatch(image):
                raise ValueError(f"{n}-sahna: rasm formati noto'g'ri (png, jpg, webp, gif)")
            html = IMG_TPL.replace("__SRC__", image)
        else:
            html = (sc.get("html") or "").strip()
            if not html:
                raise ValueError(f"{n}-sahna: HTML kod yoki rasm kerak")

        lines = []
        for ln in sc.get("lines") or []:
            if not isinstance(ln, dict):
                continue
            text = str(ln.get("text", "")).strip()
            if text:
                lines.append({"voice": ln.get("voice"), "text": text[:1000]})

        try:
            dur = float(sc.get("duration", 4))
        except (TypeError, ValueError):
            raise ValueError(f"{n}-sahna: davomiylik noto'g'ri")

        scenes.append({
            "html": html,
            "lines": lines,
            "duration": min(max(dur, 1.0), MAX_SCENE_SEC),
        })

    total = sum(s["duration"] for s in scenes)
    if total > MAX_TOTAL_SEC:
        raise ValueError(f"Jami davomiylik {MAX_TOTAL_SEC} soniyadan oshmasin")
    return scenes


def build_voice(lines, sdir):
    parts = []
    for i, ln in enumerate(lines):
        p = os.path.join(sdir, f"line{i}.mp3")
        tts(ln["text"], VOICES.get(ln.get("voice"), VOICES["uz_female"]), p)
        parts.append(p)

    lst = os.path.join(sdir, "voice.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")

    out = os.path.join(sdir, "voice.m4a")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
         "-c:a", "aac", "-ar", "44100", "-ac", "2", out])
    return out


def render_frames(page, html, dur, frames_dir):
    page.set_content(html, wait_until="load")
    for i in range(int(round(dur * FPS))):
        t = i * 1000.0 / FPS
        # CSS animatsiyalarini aniq kadrga o'tkazamiz
        page.evaluate(
            "t => document.getAnimations().forEach(a => { a.pause(); a.currentTime = t; })",
            t,
        )
        page.screenshot(path=os.path.join(frames_dir, f"f{i:05d}.png"))


def encode(frames_dir, out):
    run(["ffmpeg", "-y", "-framerate", str(FPS),
         "-i", os.path.join(frames_dir, "f%05d.png"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), out])


def mux(video, audio, volume, out):
    if audio:
        cmd = ["ffmpeg", "-y", "-i", video, "-i", audio,
               "-map", "0:v", "-map", "1:a",
               "-c:v", "copy", "-af", f"volume={volume},apad",
               "-c:a", "aac", "-shortest", out]
    else:
        cmd = ["ffmpeg", "-y", "-i", video,
               "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
               "-map", "0:v", "-map", "1:a",
               "-c:v", "copy", "-c:a", "aac", "-shortest", out]
    run(cmd)


def join_clips(clips, job):
    if len(clips) == 1:
        return clips[0]
    lst = os.path.join(job, "scenes.txt")
    with open(lst, "w") as f:
        for c in clips:
            f.write(f"file '{c}'\n")
    out = os.path.join(job, "story.mp4")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
    return out


@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True) or {}
    try:
        scenes = normalize(data)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    width, height = FORMATS.get(data.get("format"), FORMATS["youtube"])
    try:
        volume = min(max(float(data.get("volume", 1.0)), 0.2), 2.0)
    except (TypeError, ValueError):
        volume = 1.0

    job = os.path.join(OUT_DIR, uuid.uuid4().hex)
    os.makedirs(job, exist_ok=True)

    try:
        clips = []
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
            page = browser.new_page(viewport={"width": width, "height": height})
            for idx, sc in enumerate(scenes):
                sdir = os.path.join(job, f"s{idx}")
                frames = os.path.join(sdir, "frames")
                os.makedirs(frames, exist_ok=True)

                audio = build_voice(sc["lines"], sdir) if sc["lines"] else None
                dur = sc["duration"]
                if audio:
                    # Sahna ovoz tugagunча davom etadi
                    dur = min(max(dur, probe_duration(audio) + 0.6), MAX_VOICED_SEC)

                render_frames(page, sc["html"], dur, frames)

                vid = os.path.join(sdir, "v.mp4")
                encode(frames, vid)

                clip = os.path.join(sdir, "clip.mp4")
                mux(vid, audio, volume, clip)
                clips.append(clip)
            browser.close()

        out = join_clips(clips, job)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode(errors="ignore")[-500:]
        return jsonify(error=f"FFmpeg xatosi: {err}"), 500
    except Exception as e:
        return jsonify(error=f"Xatolik: {e}"), 500

    return send_file(out, mimetype="video/mp4", as_attachment=True,
                     download_name="code2video.mp4")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False)
