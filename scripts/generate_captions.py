"""
Step 4: Word-level captions with karaoke pop-highlight style (CapCut/Alan-Walker-video style).
- faster-whisper transcribes the final voice audio with word timestamps
- pysubs2 builds a styled .ass file: bold white text, active word highlighted in bright yellow,
  bottom-center placement, short 2-3 word chunks per line for readability + engagement.
Output: captions/<slug>.ass

ERROR HANDLING:
- This step is treated as NON-CRITICAL by the workflow (marked continue-on-error in the
  GitHub Actions yaml): a video with no captions is still worth uploading, a crashed pipeline
  on a daily schedule is not. If transcription fails or produces zero words, this script exits
  cleanly with a warning and simply does not write a .ass file -- make_video.py already checks
  for that file's existence and renders caption-free if it's missing.
- Model size falls back automatically: tries 'small' first (better accuracy), falls back to
  'base' if 'small' fails to load (e.g. low memory on the free GitHub runner).
"""
import sys, os, json
from faster_whisper import WhisperModel
import pysubs2

MAX_WORDS_PER_LINE = 3   # short punchy chunks = higher watch-through, matches viral caption style

def build_style(subs: pysubs2.SSAFile):
    style = pysubs2.SSAStyle()
    style.fontname = "Poppins ExtraBold"
    style.fontsize = 72
    style.bold = True
    style.primarycolor = pysubs2.Color(255, 255, 255)   # base text: white
    style.outlinecolor = pysubs2.Color(0, 0, 0)
    style.outline = 4
    style.shadow = 0
    style.alignment = 2  # bottom-center
    style.marginv = 140
    subs.styles["Default"] = style

def load_model():
    for size in ("small", "base"):
        try:
            return WhisperModel(size, device="cpu", compute_type="int8")
        except Exception as e:
            print(f"  Could not load Whisper '{size}' model ({e}), trying a smaller one...")
    return None

def main(slug: str):
    audio_path = f"voices/{slug}.mp3"
    print("→ Transcribing with faster-whisper...")

    try:
        model = load_model()
        if model is None:
            print("⚠️  No Whisper model could be loaded -- skipping captions for this video.")
            return
        segments, _ = model.transcribe(audio_path, language="hi", word_timestamps=True)
        words = []
        for seg in segments:
            for w in seg.words:
                words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
    except Exception as e:
        print(f"⚠️  Transcription failed ({e}) -- skipping captions for this video, not failing the run.")
        return

    if not words:
        print("⚠️  Transcription produced zero words -- skipping captions for this video.")
        return

    subs = pysubs2.SSAFile()
    build_style(subs)

    # group into short chunks, render karaoke \k tags so the active word pops in yellow
    for i in range(0, len(words), MAX_WORDS_PER_LINE):
        chunk = words[i:i + MAX_WORDS_PER_LINE]
        start_ms = int(chunk[0]["start"] * 1000)
        end_ms = int(chunk[-1]["end"] * 1000)

        parts = []
        for w in chunk:
            dur_cs = max(1, int((w["end"] - w["start"]) * 100))  # centiseconds, \k unit
            # \k highlights this word in yellow while it's spoken, rest stay white
            parts.append(rf"{{\k{dur_cs}\c&H00FFFF&}}{w['word']} {{\c&HFFFFFF&}}")
        line_text = "".join(parts)

        event = pysubs2.SSAEvent(start=start_ms, end=end_ms, text=line_text)
        subs.events.append(event)

    os.makedirs("captions", exist_ok=True)
    out_path = f"captions/{slug}.ass"
    subs.save(out_path)
    print(f"✅ Captions saved: {out_path} ({len(words)} words)")

if __name__ == "__main__":
    main(sys.argv[1])
