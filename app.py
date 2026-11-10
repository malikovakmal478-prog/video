import os
import uuid
import asyncio
import subprocess

from flask import Flask, request, send_file, jsonify, Response
from playwright.sync_api import sync_playwright
import edge_tts

app = Flask(__name__)

OUT_DIR = "/tmp/code2video"
os.makedirs(OUT_DIR, exist_ok=True)

FPS = 24
MAX_DURATION = 12  # Render bepul tarifida timeout bo'lmasligi uchun

FORMATS = {
    "youtube": (1920, 1080),
    "shorts": (1080, 1920),
    "square": (1080, 1080),
}

VOICES = {
    "uz_female": "uz-UZ-MadinaNeural",
    "uz_male": "uz-UZ-SardorNeural",
}

INDEX_HTML = """<!DOCTYPE html>
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
  .card { background:var(--card); border-radius:16px; padding:20px; max-width:800px; margin:auto; }
  label { display:block; margin:14px 0 6px; font-size:14px; color:#aab; }
  textarea, select, input { width:100%; background:#0e1220; color:var(--txt); border:1px solid #262c40; border-radius:10px; padding:10px; font-size:14px; }
  textarea { min-height:180px; font-family: monospace; }
  .row { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; }
  button { margin-top:18px; width:100%; padding:14px; border:0; border-radius:12px; font-size:16px; font-weight:600; color:#fff; background:linear-gradient(90deg,var(--a1),var(--a2)); cursor:pointer; }
  button:disabled { opacity:.5; cursor:wait; }
  #status { margin-top:14px; color:#8a90a6; min-height:20px; }
  #dl { display:none; margin-top:12px; padding:12px 20px; border-radius:10px; background:#10b981; color:#fff; text-decoration:none; font-weight:600; }
  @media (max-width:600px) { .row { grid-template-columns:1fr; } }
</style>
</head>
<body>
<div class="card">
  <h1>CODE2VIDEO AI</h1>
  <div class="sub">HTML/CSS animatsiyasini MP4 videoga aylantiring</div>

  <label>HTML kod (CSS ichida bo'lishi mumkin)</label>
  <textarea id="html">
<div class="box">CODE2VIDEO</div>
<style>
body{margin:0;background:#0b0d14;display:flex;align-items:center;justify-content:center;height:100vh}
.box{color:#a78bfa;font:700 96px sans-serif;animation:pop 3s ease-out forwards}
@keyframes pop{from{transform:scale(.3);opacity:0}to{transform:scale(1);opacity:1}}
</style></textarea>

  <div class="row">
    <div>
      <label>Format</label>
      <select id="format">
        <option value="youtube">YouTube 16:9 (1920x1080)</option>
        <option value="shorts">Shorts/TikTok 9:16 (1080x1920)</option>
        <option value="square">Instagram 1:1 (1080x1080)</option>
      </select>
    </div>
    <div>
      <label>Davomiylik (soniya, max 12)</label>
      <input id="duration" type="number" value="5" min="1" max="12" step="0.5">
    </div>
    <div>
      <label>Ovoz</label>
      <select id="voice">
        <option value="uz_female">O'zbekcha ayol</option>
        <option value="uz_male">O'zbekcha erkak</option>
      </select>
    </div>
  </div>

  <label>Matn (ovoz bilan o'qiladi, bo'sh qoldirsangiz ovozsiz)</label>
  <input id="narration" placeholder="Masalan: Bu CODE2VIDEO AI platformasi">

  <button id="go">Video yaratish</button>
  <div id="status"></div>
  <a id="dl" download="code2video.mp4">MP4 yuklab olish</a>
</div>

<script>
const $ = id => document.getElementById(id);
$("go").onclick = async () => {
  $("go").disabled = true;
  $("dl").style.display = "none";
  $("status").textContent = "Video yaratilmoqda, bir necha daqiqa kutishingiz mumkin...";
  const body = {
    html: $("html").value,
    format: $("format").value,
    duration: $("duration").value,
    narration: $("narration").value,
    voice: $("voice").value
  };
  try {
    const r = await fetch("/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.error || "Noma'lum xatolik");
    }
    const blob = await r.blob();
    $("dl").href = URL.createObjectURL(blob);
    $("dl").style.display = "inline-block";
    $("status").textContent = "Tayyor!";
  } catch (e) {
    $("status").textContent = "Xato: " + e.message;
  }
  $("go").disabled = false;
};
</script>
</body>
</html>"""


def render_frames(html, width, height, duration, frames_dir):
    total = int(duration * FPS)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        page = browser.new_page(viewport={"width": width, "height": height})
        page.set_content(html, wait_until="load")
        for i in range(total):
            t_ms = i * 1000.0 / FPS
            # CSS va Web Animations API animatsiyalarini aniq vaqtga o'tkazamiz
            page.evaluate(
                """t => document.getAnimations().forEach(a => {
                       a.pause(); a.currentTime = t; })""",
                t_ms,
            )
            page.screenshot(path=os.path.join(frames_dir, f"f{i:05d}.png"))
        browser.close()


def frames_to_video(frames_dir, out_path):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-framerate", str(FPS),
            "-i", os.path.join(frames_dir, "f%05d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            out_path,
        ],
        check=True, capture_output=True,
    )


async def make_voice(text, voice, out_path):
    await edge_tts.Communicate(text, voice).save(out_path)


def merge_audio(video_path, audio_path, out_path):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            out_path,
        ],
        check=True, capture_output=True,
    )


@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True) or {}
    html = (data.get("html") or "").strip()
    if not html:
        return jsonify(error="HTML kod kiriting"), 400

    fmt = data.get("format", "youtube")
    width, height = FORMATS.get(fmt, FORMATS["youtube"])

    try:
        duration = float(data.get("duration", 5))
    except (TypeError, ValueError):
        return jsonify(error="Davomiylik noto'g'ri"), 400
    duration = min(max(duration, 1.0), MAX_DURATION)

    narration = (data.get("narration") or "").strip()
    voice = VOICES.get(data.get("voice"), VOICES["uz_female"])

    job_dir = os.path.join(OUT_DIR, uuid.uuid4().hex)
    frames_dir = os.path.join(job_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    video_path = os.path.join(job_dir, "video.mp4")
    final_path = video_path

    try:
        render_frames(html, width, height, duration, frames_dir)
        frames_to_video(frames_dir, video_path)

        if narration:
            audio_path = os.path.join(job_dir, "voice.mp3")
            asyncio.run(make_voice(narration, voice, audio_path))
            final_path = os.path.join(job_dir, "final.mp4")
            merge_audio(video_path, audio_path, final_path)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode(errors="ignore")[-500:]
        return jsonify(error=f"FFmpeg xatosi: {err}"), 500
    except Exception as e:
        return jsonify(error=f"Xatolik: {e}"), 500

    return send_file(final_path, as_attachment=True, download_name="code2video.mp4")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False)
