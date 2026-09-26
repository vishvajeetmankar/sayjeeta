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

HUMANIZATION (edge-tts has no SSML/<break> support anymore -- Microsoft blocks custom SSML
as of v5+, so only rate/volume/pitch-per-call are available; everything below works within
that real constraint rather than pretending it doesn't exist):
- Small random jitter (+/-3% rate, +/-2Hz pitch, +/-4% volume) is added on top of every
  segment's emotion-based values, so two [NORMAL] lines never sound EXACTLY identical the way
  a fixed rate/pitch would -- exact repetition is a big part of what reads as "robotic".
- A short silence gap is inserted between every segment: longer (300-450ms) after a sentence
  actually ends (. ? ! ।), shorter (80-160ms) mid-thought -- simulating natural breathing/
  speech rhythm that continuous back-to-back TTS clips don't have.
Even with this, understand the ceiling: edge-tts is a free, general-purpose neural voice, not
a performance/acting model. This narrows the gap, it doesn't erase it. If "still sounds AI"
after this, the real fix is a paid expressive-TTS engine (e.g. Sarvam Bulbul) -- worth
knowing since Bulbul was intentionally skipped earlier for cost.

ERROR HANDLING:
- Each segment synth retries up to 3x (Edge-TTS occasionally drops the websocket connection)
- A segment that fails all retries is SKIPPED with a loud warning + written into
  voices/<slug>_failed_segments.json, rather than crashing the whole video -- a story missing
  one line is recoverable; a crashed pipeline on day 1 of a daily schedule is not.
- If more than 15% of segments fail, the whole step fails loudly (something systemic is wrong,
  better to stop and check than upload a half-broken audiobook).
- Final concat now re-encodes (not stream-copies) -- silence clips and retried TTS segments
  can have subtly different internal MP3 parameters, and stream-copy concat of mismatched MP3
  streams can glitch at the seams; re-encoding guarantees a clean join.
Output: voices/<slug>.mp3 + voices/<slug>_segments.json (timing map, used by captions/scenes)
"""
import os, sys, json, asyncio, re, time, random
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

async def synth_segment(text: str, voice: str, rate_pct: int, pitch: str, volume_pct: int, out_path: str, retries=3):
    rate_str = f"{'+' if rate_pct >= 0 else ''}{rate_pct}%"
    volume_str = f"{'+' if volume_pct >= 0 else ''}{volume_pct}%"
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate_str, pitch=pitch, volume=volume_str)
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

def make_silence(duration_sec: float, out_path: str) -> bool:
    duration_sec = max(0.05, duration_sec)
    ret = os.system(
        f'ffmpeg -y -f lavfi -i anullsrc=r=24000:cl=mono -t {duration_sec:.3f} '
        f'-q:a 9 -acodec libmp3lame "{out_path}" -loglevel error'
    )
    return ret == 0 and os.path.exists(out_path)

SENTENCE_END_CHARS = (".", "?", "!", "।", "…")

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

        # Small random jitter on top of the emotion preset so consecutive same-emotion lines
        # don't come out with bit-for-bit identical prosody (see module docstring).
        rate_pct = BASE_RATE + adj["rate_delta"] + random.randint(-3, 3)
        pitch_hz = base_pitch + int(adj["pitch"].replace("Hz", "").replace("+", "")) + random.randint(-2, 2)
        pitch_str = f"{'+' if pitch_hz >= 0 else ''}{pitch_hz}Hz"
        volume_pct = random.randint(-4, 4)

        part_path = f"voices/{slug}_parts/{i:04d}_{speaker}_{emotion}.mp3"
        ok = await synth_segment(text, voice, rate_pct, pitch_str, volume_pct, part_path)
        if ok:
            part_files.append(part_path)
            segment_map.append({"index": i, "speaker": speaker, "emotion": emotion, "text": text, "file": part_path})

            # Natural pause after this segment: longer if it actually ends a sentence,
            # shorter if it's mid-thought -- see module docstring's HUMANIZATION section.
            is_sentence_end = text.strip().endswith(SENTENCE_END_CHARS)
            pause_dur = random.uniform(0.30, 0.45) if is_sentence_end else random.uniform(0.08, 0.16)
            pause_path = f"voices/{slug}_parts/{i:04d}_pause.mp3"
            if make_silence(pause_dur, pause_path):
                part_files.append(pause_path)
            # if silence generation fails, we just skip the pause rather than failing the run --
            # a missing micro-pause is cosmetic, not worth aborting a whole video over
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
    # Re-encode rather than stream-copy: the silence clips and any retried TTS segments can
    # have subtly different internal MP3 parameters, and `-c copy` concatenation of mismatched
    # streams can glitch at the seams. Re-encoding to a single consistent format guarantees a
    # clean join at a small, one-time CPU cost.
    ret = os.system(f'ffmpeg -y -f concat -safe 0 -i "{concat_list}" -c:a libmp3lame -b:a 128k "{final_path}" -loglevel error')
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
