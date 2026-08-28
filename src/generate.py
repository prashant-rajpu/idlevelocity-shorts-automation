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

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def choose_topic(cfg):
    topics = [x.strip() for x in (ROOT / "data/topics.txt").read_text(encoding="utf-8").splitlines() if x.strip()]
    history = load_json(ROOT / "data/history.json")
    used_topics = {x.get("topic", "").strip().lower() for x in history}
    
    available = [t for t in topics if t.strip().lower() not in used_topics]
    if available:
        return random.choice(available), history

    print("All static topics used! Generating a fresh trending viral topic for US/Global audience via Gemini...")
    key = os.environ["GEMINI_API_KEY"]
    primary_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{primary_model}:generateContent?key={key}"

    prompt = f'''Suggest 1 NEW, highly viral, trending YouTube Shorts topic for US/Global audience in niche: {cfg['niche']}.
It must NOT be any of these previously used topics:
{json.dumps(list(used_topics)[-25:], ensure_ascii=False)}

Return JSON ONLY: {{"topic": "The single trending topic name in English"}}
'''
    try:
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}, timeout=30)
        if res.ok:
            new_topic = res.json()["candidates"][0]["content"]["parts"][0]["text"]
            topic_data = json.loads(new_topic)
            topic_name = topic_data.get("topic", "").strip()
            if topic_name and topic_name.lower() not in used_topics:
                with (ROOT / "data/topics.txt").open("a", encoding="utf-8") as f:
                    f.write(f"\n{topic_name}")
                return topic_name, history
    except Exception as e:
        print(f"Notice: Dynamic topic generation fallback: {e}")
        
    return random.choice(topics), history


def generate_script(topic, cfg):
    prompt = f'''You are a world-class viral YouTube Shorts director creating high-retention, high-CPM content for a US & Global audience on channel {cfg['channel_name']}.
Niche: {cfg['niche']}.
Topic: {topic}.
Language: Clean, punchy, conversational American English (Alex Hormozi / Andrew Huberman style).
Style: Authoritative, energetic, zero fluff, scientific & psychological framing.
Target total duration: 30 to 35 seconds (~85-110 spoken words total).

FORMAT REQUIREMENTS:
1. "hook_badge": A 3-6 word scroll-stopping English ALL-CAPS header displayed at top of screen for first 3.5s (e.g. "⚠️ NEVER DO THIS AT 6 AM ⚠️", "⚡ THE 5-SECOND FOCUS RULE", "🧠 REWIRE YOUR BRAIN TODAY").
2. 6 to 8 SHORT, FIRING SCENES. Each scene is ONLY 1 short punchy sentence (10-16 words, spoken in 2.5-4s).
3. "tags": 12-15 high-ranking YouTube keyword tags for US search & recommendations.

Return STRICT JSON ONLY with structure:
{{
  "title": "High CTR English Title with emoji (under 55 chars, e.g. 'Stop Ruining Your Mornings 🧠⚡ #Shorts')",
  "hook_badge": "Short bold top banner text in ALL CAPS",
  "description": "2-line engaging YouTube description with top hashtags",
  "tags": ["shorts", "selfimprovement", "productivity", "discipline", "mindset", "focus", "habits", "success", "psychology", "dopamine detox"],
  "scenes": [
    {{
      "text": "1 punchy, fast-spoken English line",
      "visual_query": "3-4 precise English search terms for Pexels portrait footage (e.g. 'alarm clock morning wake up', 'glowing smartphone dark bedroom', 'frustrated person laptop desk', 'putting phone away drawer', 'drinking glass cold water', 'writing journal morning desk', 'confident man sunrise walking')",
      "cinematic_prompt": "A rich 1-2 sentence description for AI text-to-video (subject, action, camera motion, lighting, color mood). Example: 'A young man in a dim bedroom slams his phone face down on the nightstand at dawn; slow dolly-in, warm rim light through blinds, teal-and-orange grade, shallow depth of field.'",
      "fallback_query": "2 general keywords (e.g. 'phone bed', 'study desk', 'morning walk')"
    }}
  ]
}}
'''

    key = os.environ["GEMINI_API_KEY"]
    primary_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    models_to_try = [primary_model, "gemini-3.6-flash", "gemini-3.1-pro-preview", "gemini-3.1-flash"]
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
                
                if "scenes" not in data or not isinstance(data["scenes"], list) or len(data["scenes"]) < 4:
                    raise ValueError("Script must contain at least 4 rapid scenes")
                
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


def format_ass_dialogues(srt_text, offset_sec, max_words_per_cue=4):
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
        
        subprocess.run(
            [
                "edge-tts",
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
            dialogue_lines = format_ass_dialogues(srt_content, current_time_offset, max_words_per_cue=4)
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
Style: HookBadge,Noto Sans,36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H900000FF,1,0,0,0,100,100,0,0,1,5,0,8,30,30,240,1
Style: CaptionText,Noto Sans,34,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,2,2,30,30,320,1

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


def render_final_video_openmontage(stock_video, voice_audio, ass_subs, bgm, cfg):
    final = OUT / "short.mp4"
    duration = get_media_duration(voice_audio)
    print(f"\nFinal Short Duration: {duration:.2f}s (Global English)")
    
    vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,subtitles='{escape_sub_path(ass_subs)}'"
    bgm_volume = cfg.get("bgm_volume", 0.14)

    if bgm and Path(bgm).exists() and bgm_volume > 0:
        print(f"Applying OpenMontage Sidechain Ducking with track: {bgm.name}")
        filter_complex = (
            f"[0:v]{vf}[vout]; "
            f"[2:a]volume={bgm_volume}[bgm_raw]; "
            f"[bgm_raw][1:a]sidechaincompress=threshold=0.12:ratio=4.5:attack=15:release=220[ducked_bgm]; "
            f"[1:a][ducked_bgm]amix=inputs=2:duration=first:dropout_transition=2,loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
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
    
    # 3. Choose background music & render final video with dynamic sidechain ducking + loudnorm
    bgm = choose_bgm()
    final_short = render_final_video_openmontage(synced_video, voice_audio, master_ass, bgm, cfg)
    
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


if __name__ == "__main__":
    main()
