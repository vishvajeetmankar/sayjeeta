"""
Step 5: Assemble the final video from MULTIPLE scene images (not one static image --
see README's YouTube policy section for why this matters for monetization).

- Each scene image gets its own slow zoom/pan segment, sized to that scene's share of
  the total voice duration (from scenes[].position_pct)
- Scenes are joined with 1-second crossfade transitions (xfade) instead of hard cuts --
  much less jarring for a 15-20 min listen
- Karaoke captions burned in
- Voice mixed with genre BGM, BGM sidechain-ducked under the voice

ERROR HANDLING:
- Building each scene clip is wrapped individually; if a scene clip fails to render, that
  scene's duration is merged into the next available good scene (video keeps going with
  fewer, slightly longer segments rather than crashing on the whole render)
- If captions (.ass file) are missing/failed from the previous step, the video is still
  rendered WITHOUT captions rather than failing entirely -- a video with no captions is
  still fine to upload; a missing video is not
- If BGM is missing, renders with voice-only audio rather than failing
- Final ffmpeg render step's return code is checked explicitly; a partial/corrupt output
  file is deleted so a broken video can never accidentally get uploaded
"""
import os, sys, json, random, subprocess, glob

TMP_DIR = "videos/_tmp"

def get_audio_duration(path: str) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path
    ])
    return float(out.strip())

def pick_bgm(genre: str):
    candidates = glob.glob(f"music/{genre}/*.mp3")
    if not candidates:
        candidates = glob.glob("music/*/*.mp3")
    return random.choice(candidates) if candidates else None

def build_scene_clip(image_path: str, duration: float, out_path: str, fps=30) -> bool:
    total_frames = max(1, int(duration * fps))
    zoompan = (
        f"zoompan=z='min(zoom+0.0006,1.35)':"
        f"x='if(gte(zoom,1.35),x,x+1)':y='if(gte(zoom,1.35),y,y+1)':"
        f"d={total_frames}:s=1920x1080:fps={fps}"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", image_path,
        "-vf", zoompan, "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not os.path.exists(out_path):
        print(f"    ⚠️  Scene clip render failed for {image_path}: {result.stderr[-300:]}")
        return False
    return True

def concat_with_crossfade(clip_paths: list, durations: list, out_path: str, fade=1.0) -> bool:
    if len(clip_paths) == 1:
        os.system(f'cp "{clip_paths[0]}" "{out_path}"')
        return os.path.exists(out_path)

    # Clamp the crossfade duration to a fraction of the SHORTEST clip. A fixed 1.0s fade
    # against a very short scene (e.g. a scene that only got ~1-2s of the story's runtime)
    # can push the xfade `offset` negative or exceed the clip's own length, which ffmpeg
    # rejects outright ("Error initializing filter 'xfade'" / negative offset). Since
    # build_scene_clip() already enforces a 1.0s minimum per scene, 0.3s is a safe fade
    # that always fits even in the worst case.
    fade = min(fade, min(durations) * 0.3)

    inputs = []
    for p in clip_paths:
        inputs += ["-i", p]

    filter_parts = []
    running_offset = durations[0] - fade
    prev_label = "[0:v]"
    for i in range(1, len(clip_paths)):
        cur_label = f"[{i}:v]"
        out_label = f"[v{i}]" if i < len(clip_paths) - 1 else "[vout]"
        filter_parts.append(
            f"{prev_label}{cur_label}xfade=transition=fade:duration={fade}:offset={running_offset}{out_label}"
        )
        prev_label = out_label
        if i < len(clip_paths) - 1:
            running_offset += durations[i] - fade

    filter_complex = ";".join(filter_parts)
    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex, "-map", "[vout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not os.path.exists(out_path):
        print(f"❌ Crossfade concat failed: {result.stderr[-500:]}")
        return False
    return True

def main(slug: str, genre: str):
    voice_path = f"voices/{slug}.mp3"
    caption_path = f"captions/{slug}.ass"
    out_path = f"videos/{slug}.mp4"
    os.makedirs(TMP_DIR, exist_ok=True)
    os.makedirs("videos", exist_ok=True)

    if not os.path.exists(voice_path):
        print(f"❌ Voice file missing at {voice_path} -- cannot build video without it.")
        sys.exit(1)

    duration = get_audio_duration(voice_path)

    with open(f"images/{slug}_scenes.json", encoding="utf-8") as f:
        scenes = json.load(f)
    scenes.sort(key=lambda s: s["position_pct"])

    # Compute each scene's slice of the total duration from position_pct
    boundaries = [s["position_pct"] / 100.0 * duration for s in scenes] + [duration]
    scene_durations = [max(1.0, boundaries[i + 1] - boundaries[i]) for i in range(len(scenes))]

    print(f"→ Building {len(scenes)} scene clips (total {duration:.0f}s)...")
    clip_paths, good_durations = [], []
    carry_over = 0.0
    for i, (scene, dur) in enumerate(zip(scenes, scene_durations)):
        this_dur = dur + carry_over
        clip_path = f"{TMP_DIR}/{slug}_clip_{i:02d}.mp4"
        ok = build_scene_clip(scene["path"], this_dur, clip_path)
        if ok:
            clip_paths.append(clip_path)
            good_durations.append(this_dur)
            carry_over = 0.0
        else:
            print(f"  Merging scene {i}'s duration into the next scene instead of failing the render.")
            carry_over += this_dur

    if not clip_paths:
        print("❌ Every scene clip failed to render -- aborting, nothing to build a video from.")
        sys.exit(1)
    if carry_over > 0:
        good_durations[-1] += carry_over  # last scene absorbs any leftover from trailing failures

    concat_path = f"{TMP_DIR}/{slug}_concat.mp4"
    print("→ Crossfading scenes together...")
    if not concat_with_crossfade(clip_paths, good_durations, concat_path):
        sys.exit(1)

    use_captions = os.path.exists(caption_path)
    if not use_captions:
        print(f"⚠️  No captions file found at {caption_path} -- rendering WITHOUT captions rather than failing.")

    bgm_path = pick_bgm(genre)
    if not bgm_path:
        print(f"⚠️  No BGM found for genre '{genre}' (check /music/{genre}/) -- rendering voice-only audio.")

    vf = "format=yuv420p"
    if use_captions:
        vf += f",ass={caption_path}"

    if bgm_path:
        # Input order below is: 0=concat_path(video only, no audio), 1=voice_path, 2=bgm_path.
        # Bug fixed: this used to reference [0:a] for the voice and [1:a] for the BGM loop,
        # but input 0 (the video-only concat) has NO audio stream at all -- ffmpeg would fail
        # with "Stream specifier ':a' in filtergraph ... matches no streams". Voice is input 1,
        # BGM is input 2.
        filter_complex = (
            f"[2:a]aloop=loop=-1:size=2e9,atrim=0:{duration}[bgm_loop];"
            f"[bgm_loop]volume=0.10[bgm_low];"
            f"[1:a][bgm_low]sidechaincompress=threshold=0.05:ratio=8:attack=5:release=300[bgm_ducked];"
            f"[1:a][bgm_ducked]amix=inputs=2:duration=first:weights=1 1[aout]"
        )
        cmd = [
            "ffmpeg", "-y", "-i", concat_path, "-i", voice_path, "-i", bgm_path,
            "-filter_complex", f"[0:v]{vf}[vout];{filter_complex}",
            "-map", "[vout]", "-map", "[aout]", "-t", str(duration),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", out_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", concat_path, "-i", voice_path,
            "-vf", vf, "-map", "0:v", "-map", "1:a", "-t", str(duration),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", out_path,
        ]

    print("→ Final render (this is the slow step, ~5-15 min)...")
    if os.path.exists(out_path):
        os.remove(out_path)  # never let a stale file from a previous failed run look like success
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 100_000:
        print(f"❌ Final render failed or produced a suspiciously small file: {result.stderr[-500:]}")
        if os.path.exists(out_path):
            os.remove(out_path)  # don't leave a broken file for the upload step to find
        sys.exit(1)

    os.system(f"rm -rf {TMP_DIR}")
    print(f"✅ Video ready: {out_path}")

if __name__ == "__main__":
    slug_arg = sys.argv[1]
    genre_arg = sys.argv[2] if len(sys.argv) > 2 else "mystery"
    main(slug_arg, genre_arg)
