import json
import os
import random
import re
import subprocess
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def choose_topic():
    topics = [x.strip() for x in (ROOT / "data/topics.txt").read_text(encoding="utf-8").splitlines() if x.strip()]
    history = load_json(ROOT / "data/history.json")
    used = {x.get("topic") for x in history[-60:]}
    available = [x for x in topics if x not in used] or topics
    return random.choice(available), history


def generate_script(topic, cfg):
    prompt = f'''Create one original YouTube Short for {cfg['channel_name']}.
Niche: {cfg['niche']}. Topic: {topic}.
Language: natural conversational Hinglish written in Devanagari Hindi, with simple English words where Indians normally use them.
Audience: Indian viewers aged 16-30.
Length: 95-120 spoken words, about {cfg['duration_target_seconds']} seconds.
Structure: 1-line pattern-interrupt hook, relatable problem, 2-3 actionable lines, memorable closing challenge.
No fake statistics, medical claims, quotes attributed to people, promises, copied catchphrases, emojis or scene directions.
Return strict JSON only with keys: title, narration, description, stock_query. Title <= 70 characters. stock_query must be 2-4 English words suitable for vertical stock footage.'''
    key = os.environ["GEMINI_API_KEY"]
    primary_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    models_to_try = [primary_model, "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]
    models_to_try = list(dict.fromkeys(models_to_try))
    
    last_err = None
    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9}}
        try:
            res = requests.post(url, json=body, timeout=90)
            if not res.ok:
                print(f"API Error ({model}) [{res.status_code}]: {res.text}")
                res.raise_for_status()
            text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
            if not 60 <= len(data["narration"].split()) <= 150:
                raise ValueError("Generated narration failed length check")
            return data
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("Failed to generate script with available Gemini models")




def download_stock(query):
    headers = {"Authorization": os.environ["PEXELS_API_KEY"]}
    res = requests.get("https://api.pexels.com/videos/search", headers=headers,
                       params={"query": query, "orientation": "portrait", "per_page": 15}, timeout=60)
    res.raise_for_status()
    videos = res.json().get("videos", [])
    if not videos:
        raise RuntimeError(f"No Pexels footage for: {query}")
    candidates = []
    for video in videos:
        for f in video.get("video_files", []):
            if f.get("file_type") == "video/mp4" and f.get("height", 0) >= f.get("width", 0):
                candidates.append((abs(f.get("height", 0) - 1920), f["link"]))
    if not candidates:
        raise RuntimeError("No portrait MP4 result")
    url = sorted(candidates)[0][1]
    target = OUT / "stock.mp4"
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with target.open("wb") as fh:
            for chunk in r.iter_content(1024 * 1024):
                fh.write(chunk)
    return target


def make_voice(narration, voice):
    audio, subs = OUT / "voice.mp3", OUT / "captions.srt"
    subprocess.run(["edge-tts", "--voice", voice, "--rate=+8%", "--text", narration,
                    "--write-media", str(audio), "--write-subtitles", str(subs)], check=True)
    return audio, subs


def escape_sub_path(path):
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_video(stock, audio, subs):
    final = OUT / "short.mp4"
    subtitle_filter = (
        f"subtitles='{escape_sub_path(subs)}':force_style='FontName=Arial,FontSize=19,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,"
        "Shadow=1,Alignment=2,MarginV=260'"
    )
    vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920," + subtitle_filter
    subprocess.run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(stock), "-i", str(audio),
                    "-vf", vf, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "medium",
                    "-crf", "21", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
                    str(final)], check=True)
    return final


def main():
    OUT.mkdir(exist_ok=True)
    cfg = load_json(ROOT / "config.json")
    topic, history = choose_topic()
    metadata = generate_script(topic, cfg)
    stock = download_stock(metadata["stock_query"])
    audio, subs = make_voice(metadata["narration"], cfg["voice"])
    video = render_video(stock, audio, subs)
    metadata.update({"topic": topic, "video": str(video), "hashtags": cfg["hashtags"]})
    (OUT / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()

