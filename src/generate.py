import json
import math
import os
import random
import shutil
import subprocess
import time
from pathlib import Path

import requests

from video_providers import generate_scene_video
try:
    from sfx_generator import build_sfx_track
except ImportError:
    from src.sfx_generator import build_sfx_track

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"


def _load_local_env():
    # Local runs read ROOT/.env (gitignored); real environment wins.
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip("'\"")
        if k and k not in os.environ:
            os.environ[k] = v


_load_local_env()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _meta_key():
    return os.getenv("META_API_KEY") or os.getenv("MODEL_API_KEY")


def _extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("No JSON object found in model output")


def _meta_complete_json(prompt, temperature=0.85, max_tokens=8000):
    key = _meta_key()
    base = os.getenv("META_API_BASE", "https://api.meta.ai/v1").rstrip("/")
    model = os.getenv("META_MODEL", "muse-spark-1.1")
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
    }
    last_err = None
    for attempt in range(3):
        try:
            res = requests.post(url, headers=headers, json=body, timeout=180)
        except requests.RequestException as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
            continue
        if res.status_code in (429, 503):
            wait_time = 2 * (attempt + 1)
            print(f"Meta API busy ({res.status_code}), retrying in {wait_time}s...")
            time.sleep(wait_time)
            continue
        if res.status_code == 400 and "max_completion_tokens" in res.text and "max_completion_tokens" in body:
            body["max_tokens"] = body.pop("max_completion_tokens")
            continue
        if not res.ok:
            last_err = RuntimeError(f"Meta API error [{res.status_code}]: {res.text[:300]}")
            break
        content = ((res.json().get("choices") or [{}])[0].get("message") or {}).get("content")
        if not content or not content.strip():
            last_err = RuntimeError("Meta model returned empty content")
            time.sleep(2 * (attempt + 1))
            continue
        return _extract_json(content)
    raise last_err or RuntimeError("Meta text generation failed")


def _complete_json(prompt, temperature=0.7):
    if _meta_key():
        return _meta_complete_json(prompt, temperature)
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("No text provider key (META_API_KEY/MODEL_API_KEY or GEMINI_API_KEY)")
    primary_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{primary_model}:generateContent?key={key}"
    res = requests.post(
        url,
        json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}},
        timeout=30,
    )
    res.raise_for_status()
    return json.loads(res.json()["candidates"][0]["content"]["parts"][0]["text"])


def choose_topic(cfg):
    topics = [x.strip() for x in (ROOT / "data/topics.txt").read_text(encoding="utf-8").splitlines() if x.strip()]
    history = load_json(ROOT / "data/history.json")
    used_topics = {x.get("topic", "").strip().lower() for x in history}
    
    available = [t for t in topics if t.strip().lower() not in used_topics]
    if available:
        return random.choice(available), history

    print("All static topics used! Generating a fresh trending viral topic for US/Global audience...")
    prompt = f'''Suggest 1 NEW, highly viral, trending YouTube Shorts topic for US/Global audience in niche: {cfg['niche']}.
It must NOT be any of these previously used topics:
{json.dumps(list(used_topics)[-25:], ensure_ascii=False)}

Return JSON ONLY: {{"topic": "The single trending topic name in English"}}
'''
    try:
        topic_data = _complete_json(prompt, temperature=0.7)
        topic_name = topic_data.get("topic", "").strip()
        if topic_name and topic_name.lower() not in used_topics:
            with (ROOT / "data/topics.txt").open("a", encoding="utf-8") as f:
                f.write(f"\n{topic_name}")
            return topic_name, history
    except Exception as e:
        print(f"Notice: Dynamic topic generation fallback: {e}")
        
    return random.choice(topics), history


def list_generate_content_models(key, timeout=20):
    # ponytail: hardcoded Gemini model IDs go stale every few months (3rd fix
    # for this exact bug); ask the API what's live instead of guessing names.
    try:
        res = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            timeout=timeout,
        )
        res.raise_for_status()
        names = [
            m["name"].split("/")[-1]
            for m in res.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        names.sort(key=lambda n: (0 if "flash" in n else 1, n))
        return names
    except Exception as e:
        print(f"Notice: could not list Gemini models ({e}); using static fallback list")
        return ["gemini-flash-latest", "gemini-pro-latest"]


def generate_script(topic, cfg):
    prompt = f'''You are an elite YouTube Shorts retention engineer creating high-CPM, viral content for channel {cfg['channel_name']}.
Niche: {cfg['niche']}.
Topic: {topic}.
Language: Punchy, urgent American English (Huberman / Hormozi cadence).
Target total duration: 28 to 32 seconds (strictly 85-100 spoken words total).

RETENTION BLUEPRINT (MANDATORY 5-PHASE STRUCTURE):
1. SCENE 1 (THE HOOK, 0-3s): Stop the scroll immediately. Use a contrarian truth or pattern interrupt. No intro fluff.
2. SCENE 2 (THE PAIN / AGITATION, 3-7s): Call out why standard self-help advice fails.
3. SCENES 3 & 4 (THE NEUROLOGICAL TRUTH, 7-15s): The biological reason (dopamine, friction, adenosine, neuroplasticity).
4. SCENES 5 & 6 (THE 1-ACTION PROTOCOL, 15-24s): Give a tangible rule they can execute today in under 60 seconds.
5. FINAL SCENE (THE SEAMLESS LOOP ANCHOR, 24-30s): The final sentence MUST grammatically flow seamlessly right back into the very first word of Scene 1 so the video loops infinitely!
   Example: If Scene 1 starts with "Your brain doesn't hate hard work...", the final scene MUST end with "...and that's the only reason why". When the video replays, it reads "...and that's the only reason why your brain doesn't hate hard work".

FORMAT REQUIREMENTS:
- Total scenes: Exactly 6 or 7 scenes.
- Exactly 1 short, fast-spoken sentence per scene (10-14 words).
- "hook_badge": 3-5 word high-urgency ALL-CAPS banner (e.g. "🧠 REWIRE YOUR BRAIN", "⚠️ NEVER CHECK THIS FIRST").
- "tags": 12-15 high-ranking search tags.

Return STRICT JSON ONLY with structure:
{{
  "title": "High CTR English Title with emoji (under 50 chars, e.g. 'Rewire Your Brain in 14 Days 🧠⚡ #Shorts')",
  "hook_badge": "3-5 word ALL CAPS banner",
  "description": "2-line engaging YouTube description with hashtags",
  "tags": ["shorts", "selfimprovement", "productivity", "discipline", "mindset", "focus", "habits", "success", "psychology", "dopamine detox"],
  "loop_connection": "Brief explanation of how the last scene loops into the first",
  "scenes": [
    {{
      "text": "1 punchy, fast-spoken English line (10-14 words)",
      "visual_query": "3-4 precise English search terms for portrait stock footage (e.g. 'exhausted student desk laptop', 'running sunrise mountain', 'intense focus eyes')",
      "cinematic_prompt": "Rich description for AI video generator: subject, action, lighting, camera movement, 9:16 vertical ratio. Example: 'Cinematic close-up of a determined young man staring intensely into a mirror at 5AM, sharp rim light, high contrast teal and orange grade, slow push-in camera.'",
      "fallback_query": "2 general keywords"
    }}
  ]
}}
'''

    if _meta_key():
        print(f"Using Meta text provider (model: {os.getenv('META_MODEL', 'muse-spark-1.1')})")
        data = _meta_complete_json(prompt, temperature=0.85)
        if "scenes" not in data or not isinstance(data["scenes"], list) or len(data["scenes"]) < 5:
            raise ValueError("Script must contain at least 5 rapid scenes")
        data["narration"] = " ".join([s["text"].strip() for s in data["scenes"] if s.get("text")])
        return data

    key = os.environ["GEMINI_API_KEY"]
    primary_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    models_to_try = [primary_model] + list_generate_content_models(key)
    models_to_try = list(dict.fromkeys(models_to_try))

    last_err = None
    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.85},
        }
        for attempt in range(3):
            try:
                res = requests.post(url, json=body, timeout=90)
                if res.status_code in (429, 503):
                    wait_time = 2 * (attempt + 1)
                    print(f"Model {model} busy ({res.status_code}), retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                if not res.ok:
                    print(f"API Error ({model}) [{res.status_code}]: {res.text}")
                    res.raise_for_status()
                text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
                data = json.loads(text)
                
                if "scenes" not in data or not isinstance(data["scenes"], list) or len(data["scenes"]) < 5:
                    raise ValueError("Script must contain at least 5 rapid scenes")
                
                data["narration"] = " ".join([s["text"].strip() for s in data["scenes"] if s.get("text")])
                return data
            except Exception as e:
                last_err = e
                break
    raise last_err or RuntimeError("Failed to generate script with available Gemini models")


def download_single_video(query, target_path, fallback_query=""):
    headers = {"Authorization": os.environ["PEXELS_API_KEY"]}
    
    queries_to_try = [query]
    if fallback_query and fallback_query != query:
        queries_to_try.append(fallback_query)
    words = query.split()
    if len(words) > 2:
        queries_to_try.append(" ".join(words[:2]))
    queries_to_try.extend(["deep focus study", "disciplined routine", "sunrise motivation", "productive workout", "calm thinking"])
    
    for q in queries_to_try:
        try:
            res = requests.get(
                "https://api.pexels.com/videos/search",
                headers=headers,
                params={"query": q, "orientation": "portrait", "per_page": 8},
                timeout=45,
            )
            if not res.ok:
                continue
            videos = res.json().get("videos", [])
            if not videos:
                continue

            candidates = []
            for video in videos:
                for f in video.get("video_files", []):
                    if f.get("file_type") == "video/mp4" and f.get("height", 0) >= f.get("width", 0):
                        candidates.append((abs(f.get("height", 0) - 1920), f["link"]))

            if candidates:
                url = sorted(candidates)[0][1]
                with requests.get(url, stream=True, timeout=120) as r:
                    r.raise_for_status()
                    with target_path.open("wb") as fh:
                        for chunk in r.iter_content(1024 * 1024):
                            fh.write(chunk)
                return target_path
        except Exception:
            continue

    raise RuntimeError(f"Could not find footage for: {query}")


def get_media_duration(file_path):
    res = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path)
        ],
        capture_output=True,
        text=True,
        check=True
    )
    return float(res.stdout.strip())


def sec_to_ass_time(sec):
    hrs = int(sec // 3600)
    mins = int((sec % 3600) // 60)
    secs = int(sec % 60)
    centis = int(round((sec - int(sec)) * 100))
    return f"{hrs:d}:{mins:02d}:{secs:02d}.{centis:02d}"


def srt_time_to_sec(ts):
    parts = ts.replace(",", ".").split(":")
    return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])


def format_ass_dialogues(srt_text, offset_sec, max_words_per_cue=2):
    lines = srt_text.strip().splitlines()
    dialogues = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.isdigit():
            i += 1
            if i >= len(lines):
                break
            time_line = lines[i].strip()
            if "-->" in time_line:
                t1, t2 = [x.strip() for x in time_line.split("-->")]
                s1 = srt_time_to_sec(t1) + offset_sec
                s2 = srt_time_to_sec(t2) + offset_sec
                i += 1
                text_lines = []
                while i < len(lines) and lines[i].strip() and not lines[i].strip().isdigit():
                    text_lines.append(lines[i].strip())
                    i += 1
                
                full_text = " ".join(text_lines).upper()
                words = full_text.split()
                if len(words) > max_words_per_cue:
                    num_chunks = math.ceil(len(words) / max_words_per_cue)
                    chunk_dur = (s2 - s1) / num_chunks
                    for c_idx in range(num_chunks):
                        c_words = words[c_idx * max_words_per_cue : (c_idx + 1) * max_words_per_cue]
                        cs1 = s1 + c_idx * chunk_dur
                        cs2 = min(cs1 + chunk_dur, s2)
                        dialogues.append(f"Dialogue: 0,{sec_to_ass_time(cs1)},{sec_to_ass_time(cs2)},CaptionText,,0,0,0,,{' '.join(c_words)}")
                else:
                    dialogues.append(f"Dialogue: 0,{sec_to_ass_time(s1)},{sec_to_ass_time(s2)},CaptionText,,0,0,0,,{full_text}")
        else:
            i += 1
    return dialogues


def generate_scene_audio_and_ass(scenes, hook_badge, voice, rate="+18%"):
    audio_files = []
    scene_durations = []
    all_caption_dialogues = []
    current_time_offset = 0.0

    for idx, scene in enumerate(scenes):
        scene_audio = OUT / f"scene_{idx}_audio.mp3"
        scene_subs = OUT / f"scene_{idx}_subs.srt"
        
        edge_bin = shutil.which("edge-tts") or str(ROOT / ".venv/bin/edge-tts")
        subprocess.run(
            [
                edge_bin,
                "--voice", voice,
                f"--rate={rate}",
                "--text", scene["text"],
                "--write-media", str(scene_audio),
                "--write-subtitles", str(scene_subs),
            ],
            check=True,
        )
        
        dur = get_media_duration(scene_audio)
        scene_durations.append(dur)
        audio_files.append(scene_audio)
        
        if scene_subs.exists():
            srt_content = scene_subs.read_text(encoding="utf-8")
            dialogue_lines = format_ass_dialogues(srt_content, current_time_offset, max_words_per_cue=2)
            all_caption_dialogues.extend(dialogue_lines)

        current_time_offset += dur

    concat_audio_txt = OUT / "concat_audio.txt"
    concat_audio_txt.write_text("\n".join([f"file '{p.resolve()}'" for p in audio_files]), encoding="utf-8")
    
    master_audio = OUT / "voice.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_audio_txt),
        "-c", "copy", str(master_audio)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    hook_end = min(3.5, current_time_offset)
    ass_template = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: HookBadge,Noto Sans,38,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,1,0,0,0,100,100,0,0,1,4,0,8,40,40,240,1
Style: CaptionText,Noto Sans,42,&H0000FFFF,&H00FFFFFF,&H00000000,&HA0000000,1,0,0,0,100,100,0,0,1,5,2,2,40,40,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 1,0:00:00.00,{sec_to_ass_time(hook_end)},HookBadge,,0,0,0,,{hook_badge}
"""
    ass_full = ass_template + "\n".join(all_caption_dialogues)
    master_ass = OUT / "captions.ass"
    master_ass.write_text(ass_full, encoding="utf-8")

    return master_audio, master_ass, scene_durations


def build_script_matching_video(scenes, scene_durations, cfg):
    scene_video_clips = []
    provider_usage: dict[str, int] = {}

    for idx, (scene, duration) in enumerate(zip(scenes, scene_durations)):
        raw_clip = OUT / f"raw_scene_{idx}.mp4"
        synced_clip = OUT / f"synced_scene_{idx}.mp4"

        query = scene.get("visual_query", "discipline focus motivation")
        print(f"🎬 Scene {idx+1}/{len(scenes)} [{duration:.2f}s]: '{query}'")

        _, used = generate_scene_video(scene, raw_clip, duration, cfg)
        provider_usage[used] = provider_usage.get(used, 0) + 1
        
        subprocess.run([
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(raw_clip),
            "-t", f"{duration:.2f}",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
            "-r", "30", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            str(synced_clip)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        scene_video_clips.append(synced_clip)

    concat_video_txt = OUT / "concat_video.txt"
    concat_video_txt.write_text("\n".join([f"file '{p.resolve()}'" for p in scene_video_clips]), encoding="utf-8")
    
    master_video = OUT / "stock_synced.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_video_txt),
        "-c", "copy", str(master_video)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if provider_usage:
        summary = ", ".join(f"{n}×{c}" for n, c in provider_usage.items())
        print(f"📦 Providers used: {summary}")

    return master_video


def choose_bgm():
    bgm_dir = ROOT / "data/bgm"
    if bgm_dir.exists():
        energy_tracks = list(bgm_dir.glob("*energy*.mp3")) + list(bgm_dir.glob("*pulse*.mp3"))
        if energy_tracks:
            return random.choice(energy_tracks)
        tracks = list(bgm_dir.glob("*.mp3"))
        if tracks:
            return random.choice(tracks)
    return None


def escape_sub_path(path):
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_final_video_openmontage(stock_video, voice_audio, ass_subs, bgm, cfg, scene_durations=None):
    final = OUT / "short.mp4"
    duration = get_media_duration(voice_audio)
    print(f"\nFinal Short Duration: {duration:.2f}s (Global English)")
    
    vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,subtitles='{escape_sub_path(ass_subs)}'"
    bgm_volume = cfg.get("bgm_volume", 0.14)

    sfx_track = None
    if scene_durations and len(scene_durations) > 1:
        try:
            sfx_track = build_sfx_track(scene_durations, duration, OUT / "sfx_track.wav")
            print(f"🔊 Built sound design track ({len(scene_durations)-1} transition whooshes + opening impact boom)")
        except Exception as e:
            print(f"⚠️ SFX generation skipped: {e}")
            sfx_track = None

    has_bgm = bool(bgm and Path(bgm).exists() and bgm_volume > 0)
    has_sfx = bool(sfx_track and Path(sfx_track).exists())

    if has_bgm and has_sfx:
        print(f"Applying OpenMontage Multi-Track Mix (Voice + Ducked BGM: {bgm.name} + SFX Sound Design)")
        filter_complex = (
            f"[0:v]{vf}[vout]; "
            f"[2:a]volume={bgm_volume}[bgm_raw]; "
            f"[bgm_raw][1:a]sidechaincompress=threshold=0.12:ratio=4.5:attack=15:release=220[ducked_bgm]; "
            f"[1:a][ducked_bgm][3:a]amix=inputs=3:duration=first:dropout_transition=2,loudnorm=I=-14:TP=-1.0:LRA=11[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(stock_video),
            "-i", str(voice_audio),
            "-stream_loop", "-1", "-i", str(bgm),
            "-i", str(sfx_track),
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.2f}",
            "-movflags", "+faststart",
            str(final)
        ]
    elif has_bgm:
        print(f"Applying OpenMontage Sidechain Ducking with track: {bgm.name}")
        filter_complex = (
            f"[0:v]{vf}[vout]; "
            f"[2:a]volume={bgm_volume}[bgm_raw]; "
            f"[bgm_raw][1:a]sidechaincompress=threshold=0.12:ratio=4.5:attack=15:release=220[ducked_bgm]; "
            f"[1:a][ducked_bgm]amix=inputs=2:duration=first:dropout_transition=2,loudnorm=I=-14:TP=-1.0:LRA=11[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(stock_video),
            "-i", str(voice_audio),
            "-stream_loop", "-1", "-i", str(bgm),
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.2f}",
            "-movflags", "+faststart",
            str(final)
        ]
    elif has_sfx:
        print("Applying OpenMontage Audio Mix (Voice + SFX Sound Design)")
        filter_complex = (
            f"[0:v]{vf}[vout]; "
            f"[1:a][2:a]amix=inputs=2:duration=first:dropout_transition=2,loudnorm=I=-14:TP=-1.0:LRA=11[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(stock_video),
            "-i", str(voice_audio),
            "-i", str(sfx_track),
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.2f}",
            "-movflags", "+faststart",
            str(final)
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(stock_video),
            "-i", str(voice_audio),
            "-vf", vf,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.2f}",
            "-movflags", "+faststart",
            str(final)
        ]

    subprocess.run(cmd, check=True)
    return final


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(exist_ok=True)
    cfg = load_json(ROOT / "config.json")
    topic, history = choose_topic(cfg)
    print(f"\n=======================================================")
    print(f"🎬 US/Global Pipeline: Generating Short '{topic}'")
    print(f"=======================================================")
    
    script_data = generate_script(topic, cfg)
    scenes = script_data["scenes"]
    hook_badge = script_data.get("hook_badge", "⚡ 99% OF PEOPLE DO THIS WRONG")
    print(f"Hook Badge: {hook_badge}")
    print(f"Generated {len(scenes)} fast-firing scenes.")
    
    voice = cfg.get("voice", "en-US-ChristopherNeural")
    rate = cfg.get("speech_rate", "+18%")
    
    # 1. Synthesize audio per scene & build ASS subtitles with top hook badge
    voice_audio, master_ass, scene_durations = generate_scene_audio_and_ass(
        scenes,
        hook_badge,
        voice=voice,
        rate=rate
    )
    
    # 2. Build precision visual track matching each scene sentence
    synced_video = build_script_matching_video(scenes, scene_durations, cfg)
    
    # 3. Choose background music & render final video with dynamic sidechain ducking + loudnorm + SFX
    bgm = choose_bgm()
    final_short = render_final_video_openmontage(synced_video, voice_audio, master_ass, bgm, cfg, scene_durations=scene_durations)
    
    metadata = {
        "title": script_data["title"],
        "description": script_data["description"],
        "narration": script_data["narration"],
        "hook_badge": hook_badge,
        "scenes": scenes,
        "tags": script_data.get("tags", cfg.get("hashtags", ["shorts", "selfimprovement"])),
        "topic": topic,
        "video": str(final_short),
        "hashtags": cfg.get("hashtags", ["#shorts", "#selfimprovement", "#discipline", "#productivity", "#mindset"])
    }
    (OUT / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n✓ US/Global High-Retention Short ready!")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))

    phone_export = Path("/mnt/sdcard/Download")
    if phone_export.exists():
        try:
            target_export = phone_export / "idlevelocity_retention_short.mp4"
            shutil.copyfile(final_short, target_export)
            print(f"\n📱 Exported to phone Download folder: {target_export}")
        except Exception as e:
            print(f"⚠️ Could not copy to phone Download: {e}")


if __name__ == "__main__":
    main()
