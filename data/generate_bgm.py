import math
import struct
import wave
from pathlib import Path
import subprocess

OUT_DIR = Path(__file__).resolve().parent / "bgm"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE_RATE = 44100

def generate_driving_energy_track(filename, chords, bpm=118, total_bars=16):
    """
    Generate an upbeat, rhythmic, high-energy driving electronic focus beat.
    """
    sec_per_beat = 60.0 / bpm
    bar_sec = sec_per_beat * 4
    total_sec = total_bars * bar_sec
    total_samples = int(total_sec * SAMPLE_RATE)
    
    samples = [0.0] * total_samples
    
    # 1. Add Chords & Bass
    for bar_idx in range(total_bars):
        chord = chords[bar_idx % len(chords)]
        start_sample = int(bar_idx * bar_sec * SAMPLE_RATE)
        chord_samples = int(bar_sec * SAMPLE_RATE)
        
        # Bass note (root / octave lower)
        root_freq = chord[0] / 2.0
        
        # 4 beats per bar
        for beat in range(4):
            b_start = start_sample + int(beat * sec_per_beat * SAMPLE_RATE)
            b_len = int(sec_per_beat * SAMPLE_RATE)
            
            # Bass pulse on every beat
            for s in range(b_len):
                idx = b_start + s
                if idx >= total_samples:
                    break
                t = s / SAMPLE_RATE
                env_bass = math.exp(-6.0 * t / sec_per_beat)
                val_bass = 0.5 * math.sin(2 * math.pi * root_freq * t) + 0.3 * math.sin(2 * math.pi * (root_freq * 2) * t)
                samples[idx] += val_bass * env_bass * 0.4
                
            # Kick drum on beats 0 & 2
            if beat in (0, 2):
                kick_len = int(0.18 * SAMPLE_RATE)
                for s in range(kick_len):
                    idx = b_start + s
                    if idx >= total_samples:
                        break
                    t = s / SAMPLE_RATE
                    freq = 130 * math.exp(-25.0 * t) + 45
                    env_k = math.exp(-18.0 * t)
                    samples[idx] += 0.7 * math.sin(2 * math.pi * freq * t) * env_k
                    
            # Hi-hat on off-beats (0.5, 1.5, 2.5, 3.5)
            hat_start = b_start + int(0.5 * sec_per_beat * SAMPLE_RATE)
            hat_len = int(0.06 * SAMPLE_RATE)
            for s in range(hat_len):
                idx = hat_start + s
                if idx >= total_samples:
                    break
                t = s / SAMPLE_RATE
                env_h = math.exp(-40.0 * t)
                # white noise burst
                noise = ((hash(idx) % 2000) / 1000.0 - 1.0)
                samples[idx] += 0.25 * noise * env_h

        # Synth Pad layer
        for freq in chord:
            for s in range(chord_samples):
                idx = start_sample + s
                if idx >= total_samples:
                    break
                t = s / SAMPLE_RATE
                env_pad = 0.5 * (1 - math.cos(2 * math.pi * t / bar_sec))
                val_pad = 0.25 * math.sin(2 * math.pi * freq * t) + 0.1 * math.sin(2 * math.pi * (freq * 1.004) * t)
                samples[idx] += val_pad * env_pad * 0.2

    # Normalize
    max_val = max(abs(x) for x in samples) or 1.0
    wav_path = OUT_DIR / (filename + ".wav")
    mp3_path = OUT_DIR / (filename + ".mp3")
    
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        int_samples = [int(min(max(s / max_val * 0.88, -1.0), 1.0) * 32767) for s in samples]
        wf.writeframes(struct.pack(f"<{len(int_samples)}h", *int_samples))
    
    subprocess.run([
        "ffmpeg", "-y", "-i", str(wav_path),
        "-c:a", "libmp3lame", "-b:a", "192k", str(mp3_path)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wav_path.unlink(missing_ok=True)
    print(f"Generated high-energy track: {mp3_path}")


def main():
    # Am -> F -> C -> G
    chords_energy = [
        [220.00, 261.63, 329.63], # Am
        [174.61, 220.00, 261.63], # F
        [130.81, 164.81, 196.00], # C
        [196.00, 246.94, 293.66], # G
    ]
    generate_driving_energy_track("drive_energy", chords_energy, bpm=120, total_bars=16)

    # Dm -> Bb -> F -> C
    chords_pulse = [
        [146.83, 174.61, 220.00], # Dm
        [116.54, 146.83, 174.61], # Bb
        [174.61, 220.00, 261.63], # F
        [130.81, 164.81, 196.00], # C
    ]
    generate_driving_energy_track("action_pulse", chords_pulse, bpm=124, total_bars=16)


if __name__ == "__main__":
    main()
