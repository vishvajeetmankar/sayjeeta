"""
Step 2: Convert the tagged script into voice using Edge-TTS (free, unlimited).

Speaker -> voice (FEMALE ONLY -- the channel is a single-narrator audiobook, no male voice.
If a [MALE] tag ever slips through from an older cached prompt, it's mapped to the SAME
female voice rather than crashing, so nothing breaks):
  [NARRATOR] / [FEMALE] / [MALE] -> hi-IN-SwaraNeural

A fixed, hardcoded self-introduction ("main Sayjeeta...") is spliced in as segment index 1,
right after the hook (segment index 0 -- see prompts/story_prompt.txt rule 1, which requires
the model to write the entire hook as one single tagged line specifically so this splice
point is reliable). Keeping this line hardcoded rather than regenerated per-story is
intentional: consistent channel branding matters more here than per-episode variety, and it
guarantees the intro can never come out grammatically broken the way freshly-generated model
text sometimes has.

Emotion tag -> rate/pitch nudge (this is what makes it "perform" instead of "read" --
Edge-TTS has no native emotion parameter, so we fake it with prosody, same trick real
Hindi-audio-story creators use with any TTS engine):
  [NORMAL]   base rate/pitch
  [DRAMATIC] slower, lower pitch
  [WHISPER]  much slower, lower volume + pitch
  [EXCITED]  faster, higher pitch
  [SAD]      slower, slightly lower pitch

ERROR HANDLING:
- Each segment synth retries up to 3x (Edge-TTS occasionally drops the websocket connection)
- A segment that fails all retries is SKIPPED with a loud warning + written into
  voices/<slug>_failed_segments.json, rather than crashing the whole video -- a story missing
  one line is recoverable; a crashed pipeline on day 1 of a daily schedule is not.
- If more than 15% of segments fail, the whole step fails loudly (something systemic is wrong,
  better to stop and check than upload a half-broken audiobook).
Output: voices/<slug>.mp3 + voices/<slug>_segments.json (timing map, used by captions/scenes)
"""
import os, sys, json, asyncio, re, time
import edge_tts

VOICE_MAP = {
    "NARRATOR": "hi-IN-SwaraNeural",
    "FEMALE": "hi-IN-SwaraNeural",
    "MALE": "hi-IN-SwaraNeural",  # no male voice on this channel -- see module docstring
}
BASE_RATE = 20  # % -> 1.2x speed (raised from 1.15x per user's request)

FIXED_INTRO_TEXT = (
    "नमस्कार दोस्तों! मैं हूं सजीता, और आप सुन रहे हैं Sayjeeta Stories। "
    "आज की कहानी शुरू करते हैं।"
)

EMOTION_ADJUST = {
    "NORMAL":   {"rate_delta": 0,   "pitch": "+0Hz"},
    "DRAMATIC": {"rate_delta": -8,  "pitch": "-4Hz"},
    "WHISPER":  {"rate_delta": -15, "pitch": "-6Hz"},
    "EXCITED":  {"rate_delta": +10, "pitch": "+5Hz"},
    "SAD":      {"rate_delta": -10, "pitch": "-3Hz"},
}

GENRE_PITCH_BASE = {
    "horror": -3, "mystery": -1, "emotional": 0, "motivational": 4, "thriller": -2,
}

LINE_RE = re.compile(r"^\[(NARRATOR|MALE|FEMALE)\]\[(NORMAL|DRAMATIC|WHISPER|EXCITED|SAD)\]:?\s*(.*)$")
# fallback for lines missing the emotion tag, so a small model formatting slip doesn't crash the run
LINE_RE_SIMPLE = re.compile(r"^\[(NARRATOR|MALE|FEMALE)\]:?\s*(.*)$")

def parse_script(script: str):
    segments = []
    for line in script.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = LINE_RE.match(line)
        if m:
            speaker, emotion, text = m.group(1), m.group(2), m.group(3).strip().strip('"')
            if text:
                segments.append((speaker, emotion, text))
            continue
        m2 = LINE_RE_SIMPLE.match(line)
        if m2:
            speaker, text = m2.group(1), m2.group(2).strip().strip('"')
            if text:
                segments.append((speaker, "NORMAL", text))
            continue
        # No recognizable tag at all -- treat as plain narration rather than dropping the line
        segments.append(("NARRATOR", "NORMAL", line))
    return segments

async def synth_segment(text: str, voice: str, rate_pct: int, pitch: str, out_path: str, retries=3):
    rate_str = f"{'+' if rate_pct >= 0 else ''}{rate_pct}%"
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate_str, pitch=pitch)
            await communicate.save(out_path)
            if os.path.getsize(out_path) < 200:  # near-empty file = silent failure, edge-tts does this occasionally
                raise IOError("Output file suspiciously small, treating as failed synth")
            return True
        except Exception as e:
            last_err = e
            print(f"    retry {attempt}/{retries} for segment failed: {e}")
            await asyncio.sleep(2 * attempt)
    print(f"    ❌ segment permanently failed after {retries} retries: {last_err}")
    return False

async def main(slug: str, genre: str):
    with open(f"stories/{slug}.json", encoding="utf-8") as f:
        story = json.load(f)

    segments = parse_script(story["story_script"])
    if not segments:
        print("❌ No segments parsed from story_script -- aborting.")
        sys.exit(1)

    # Splice the fixed brand intro right after segment 0 (the hook -- guaranteed to be a
    # single segment by prompts/story_prompt.txt rule 1). If the model ever violates that
    # rule and the "hook" ends up spanning multiple segments, this still just inserts the
    # intro after the first one -- not perfect, but never crashes.
    segments.insert(1, ("NARRATOR", "NORMAL", FIXED_INTRO_TEXT))

    base_pitch = GENRE_PITCH_BASE.get(genre, 0)

    os.makedirs(f"voices/{slug}_parts", exist_ok=True)
    part_files, segment_map, failed = [], [], []

    for i, (speaker, emotion, text) in enumerate(segments):
        voice = VOICE_MAP.get(speaker, "hi-IN-SwaraNeural")
        adj = EMOTION_ADJUST.get(emotion, EMOTION_ADJUST["NORMAL"])
        rate_pct = BASE_RATE + adj["rate_delta"]
        pitch_hz = base_pitch + int(adj["pitch"].replace("Hz", "").replace("+", ""))
        pitch_str = f"{'+' if pitch_hz >= 0 else ''}{pitch_hz}Hz"

        part_path = f"voices/{slug}_parts/{i:04d}_{speaker}_{emotion}.mp3"
        ok = await synth_segment(text, voice, rate_pct, pitch_str, part_path)
        if ok:
            part_files.append(part_path)
            segment_map.append({"index": i, "speaker": speaker, "emotion": emotion, "text": text, "file": part_path})
        else:
            failed.append({"index": i, "speaker": speaker, "text": text})
        print(f"  [{i}] {speaker}/{emotion}: {text[:40]}... {'OK' if ok else 'FAILED'}")

    fail_rate = len(failed) / max(1, len(segments))
    if fail_rate > 0.15:
        print(f"❌ {len(failed)}/{len(segments)} segments failed ({fail_rate:.0%}) -- something is systemically "
              f"wrong (check Edge-TTS status / network). Aborting rather than uploading a broken audiobook.")
        sys.exit(1)

    if failed:
        with open(f"voices/{slug}_failed_segments.json", "w", encoding="utf-8") as f:
            json.dump(failed, f, ensure_ascii=False, indent=2)
        print(f"⚠️  {len(failed)} segment(s) skipped, logged to voices/{slug}_failed_segments.json")

    concat_list = f"voices/{slug}_concat.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in part_files:
            f.write(f"file '{os.path.abspath(p)}'\n")

    final_path = f"voices/{slug}.mp3"
    ret = os.system(f'ffmpeg -y -f concat -safe 0 -i "{concat_list}" -c copy "{final_path}"')
    if ret != 0 or not os.path.exists(final_path):
        print("❌ ffmpeg concat failed -- see log above.")
        sys.exit(1)

    with open(f"voices/{slug}_segments.json", "w", encoding="utf-8") as f:
        json.dump(segment_map, f, ensure_ascii=False, indent=2)

    print(f"✅ Voice saved: {final_path}")

if __name__ == "__main__":
    slug_arg = sys.argv[1]
    genre_arg = sys.argv[2] if len(sys.argv) > 2 else "mystery"
    asyncio.run(main(slug_arg, genre_arg))
